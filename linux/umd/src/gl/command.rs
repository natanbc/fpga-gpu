use std::cell::RefCell;
use std::rc::Rc;
use mycelium_bitfield::Pack32;
use crate::gl::common::ScreenVertex;
use crate::hal::{DmaBuf, MemoryMap, Rasterizer, Userdma};

const BUFFER_SIZE_WORDS: usize = 8192;
const BUFFER_COUNT: usize = 2;

pub(crate) struct CommandBuffer {
    rasterizer: Rc<RefCell<Rasterizer>>,
    buffers: [Buffer; BUFFER_COUNT],
    current_buffer: usize,
    current_pos: usize,
}

impl CommandBuffer {
    const OPCODE: Pack32 = Pack32::least_significant(6);
    const DT_TEXTURE_ENABLE: Pack32 = Self::OPCODE.next(1);
    const DT_TEXTURE_BUFFER: Pack32 = Self::DT_TEXTURE_ENABLE.next(2);
    const LT_BUFFER: Pack32 = Self::OPCODE.next(2);
    const LT_S_HIGH: Pack32 = Self::LT_BUFFER.next(1);
    const LT_S_START: Pack32 = Self::LT_S_HIGH.next(6);
    const LT_S_END: Pack32 = Self::LT_S_START.next(6);
    const LT_T_HIGH: Pack32 = Self::LT_S_END.next(1);
    const LT_T_START: Pack32 = Self::LT_T_HIGH.next(5);
    const LT_T_END: Pack32 = Self::LT_T_START.next(5);
    const CB_PAD: Pack32 = Self::OPCODE.next(2);
    const CB_PATTERN: Pack32 = Self::CB_PAD.next(24);

    pub fn new(rasterizer: Rc<RefCell<Rasterizer>>, alloc: &Userdma) -> std::io::Result<Self> {
        let mut s = Self {
            rasterizer: rasterizer,
            buffers: [
                Buffer::new(alloc)?,
                Buffer::new(alloc)?,
            ],
            current_buffer: 0,
            current_pos: 0,
        };
        s.buffers[s.current_buffer].dma_buf.sync_start();
        Ok(s)
    }

    pub async fn draw_triangle(&mut self, texture: Option<u8>, v0: ScreenVertex, v1: ScreenVertex, v2: ScreenVertex) {
        let cmd = Pack32::pack_in(0)
            .pack(0x01, &Self::OPCODE)
            .pack(texture.is_some() as u32, &Self::DT_TEXTURE_ENABLE)
            .pack(texture.unwrap_or(0) as u32, &Self::DT_TEXTURE_BUFFER)
            .bits();
        let mut data = [0; 7];
        data[0] = cmd;
        for (i, v) in [v0, v1, v2].iter().enumerate() {
            let (w0, w1) = v.pack();
            data[i * 2 + 1] = w0;
            data[i * 2 + 2] = w1;
        }
        self.write_raw_slice(&data[..]).await;
    }

    pub async fn load_texture(&mut self, buffer: u8, start_s: u8, end_s: u8, start_t: u8, end_t: u8, data: &[u8]) {
        assert!(buffer < 4);

        assert!(start_s < 128);
        assert!(end_s < 128);
        assert!(start_s <= end_s);

        assert!(start_t < 128);
        assert!(end_t < 128);
        assert_eq!(start_t % 2, 0);
        assert_eq!(end_t % 2, 1);

        let s_high = start_s >> 6;
        assert_eq!(s_high, end_s >> 6);

        let start_t_half = start_t / 2;
        let end_t_half = end_t / 2;
        assert!(start_t_half < end_t_half);
        let t_high = start_t_half >> 5;
        assert_eq!(t_high, end_t_half >> 5);

        let expected_len = (end_s - start_s + 1) as usize * ((end_t_half - start_t_half + 1) as usize * 2) * 3;
        assert_eq!(data.len(), expected_len);

        let cmd = Pack32::pack_in(0)
            .pack(0x02, &Self::OPCODE)
            .pack(buffer as u32, &Self::LT_BUFFER)
            .pack(s_high as u32, &Self::LT_S_HIGH)
            .pack((start_s & 0x3F) as u32, &Self::LT_S_START)
            .pack((end_s & 0x3F) as u32, &Self::LT_S_END)
            .pack(t_high as u32, &Self::LT_T_HIGH)
            .pack((start_t_half & 0x1F) as u32, &Self::LT_T_START)
            .pack((end_t_half & 0x1F) as u32, &Self::LT_T_END)
            .bits();
        self.write_raw(cmd).await;

        let mut words: &[u32] = bytemuck::cast_slice(data);
        while words.len() > 0 {
            let written = self.write_raw_slice(words).await;
            words = &words[written..];
        }
    }

    pub async fn wait_idle(&mut self) {
        self.write_raw(0x03).await;
    }

    pub async fn clear_buffer(&mut self, addr: u32, words: u32, pattern: u32) {
        let cmd = Pack32::pack_in(0)
            .pack(0x04, &Self::OPCODE)
            .pack(pattern, &Self::CB_PATTERN)
            .bits();
        self.write_raw_slice(&[
            cmd,
            addr,
            words
        ]).await;
    }

    pub async fn wait_clear_idle(&mut self) {
        self.write_raw(0x05).await;
    }

    pub async fn write_raw(&mut self, val: u32) {
        self.maybe_flip_buffers().await;
        let pos = self.current_pos;
        self.buffers[self.current_buffer].write(pos, val);
        self.current_pos += 1;
    }

    pub async fn write_raw_slice(&mut self, vals: &[u32]) -> usize {
        self.maybe_flip_buffers().await;
        let written = (BUFFER_SIZE_WORDS - self.current_pos).min(vals.len());

        let pos = self.current_pos;
        self.buffers[self.current_buffer].write_slice(pos, &vals[..written]);
        self.current_pos += written;
        written
    }

    pub async fn flush_buffer(&mut self) {
        if self.current_pos != 0 {
            self.buffers[self.current_buffer].dma_buf.sync_end();

            {
                let mut rast = self.rasterizer.borrow_mut();
                rast.wait_cmd_dma().await;
                unsafe {
                    rast.submit_commands(self.buffers[self.current_buffer].phys, self.current_pos);
                }
            }

            self.current_buffer = (self.current_buffer + 1) % BUFFER_COUNT;
            self.current_pos = 0;
            self.buffers[self.current_buffer].dma_buf.sync_start();
        }
    }

    async fn maybe_flip_buffers(&mut self) {
        if self.current_pos == BUFFER_SIZE_WORDS {
            self.flush_buffer().await;
        }
    }
}

struct Buffer {
    dma_buf: DmaBuf,
    phys: usize,
    map: MemoryMap,
}

impl Buffer {
    fn new(alloc: &Userdma) -> std::io::Result<Self> {
        let (mut dma_buf, phys) = alloc.alloc_buf(4 * BUFFER_SIZE_WORDS)?;
        let map = dma_buf.map()?;
        Ok(Self {
            dma_buf,
            phys,
            map,
        })
    }

    fn write(&mut self, idx: usize, val: u32) {
        debug_assert!(idx < BUFFER_SIZE_WORDS);
        let ptr = *self.map as *mut u32;
        unsafe {
            ptr.add(idx).write(val);
        }
    }

    fn write_slice(&mut self, idx: usize, vals: &[u32]) {
        debug_assert!(idx + vals.len() <= BUFFER_SIZE_WORDS);
        let ptr = *self.map as *mut u32;
        unsafe {
            core::ptr::copy_nonoverlapping(vals.as_ptr(), ptr.add(idx), vals.len());
        }
    }
}
