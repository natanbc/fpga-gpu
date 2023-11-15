use std::cell::{Cell, RefCell};
use std::rc::Rc;
use glam::Mat4;
use crate::gl::command::CommandBuffer;
use crate::hal::{DmaBuf, Rasterizer, Uio, Userdma};
use crate::gl::common::GlCommon;

use crate::gl::common::{
    GouraudVertex,
    TextureVertex,
    CullMode,
    FrontFace
};

pub struct TextureBuffer {
    id: u32,
    data: Vec<u8>,
    dirty: Cell<bool>,
}

fn pixel_index(x: u8, y: u8) -> usize {
    assert!(x < 128, "Texture index {} out of bounds", x);
    assert!(y < 128, "Texture index {} out of bounds", y);
    let quad = match (x >> 6, y >> 6) {
        (0, 0) => 0,
        (1, 0) => 1,
        (0, 1) => 2,
        (1, 1) => 3,
        _ => unreachable!(),
    };
    let x_quad = (x & 0x3F) as usize;
    let y_quad = (y & 0x3F) as usize;
    (quad * 64 * 64 + y_quad * 64 + x_quad) * 3
}

impl TextureBuffer {
    fn new(id: u32) -> Self {
        Self {
            id,
            data: vec![0u8; 128 * 128 * 3],
            dirty: Cell::new(true),
        }
    }

    pub fn load(&mut self, rgb: &[u8]) {
        assert_eq!(rgb.len(), 128 * 128 * 3);
        for y in 0..128 {
            for x in 0..128 {
                let src_idx = (y as usize * 128 + x as usize) * 3;
                let dst_idx = pixel_index(x, y);
                (&mut self.data[dst_idx..dst_idx + 3]).copy_from_slice(&rgb[src_idx..src_idx + 3]);
            }
        }
        self.dirty.set(true);
    }
}

pub struct Gl {
    common: GlCommon,

    rasterizer: Rc<RefCell<Rasterizer>>,

    next_texture_buffer_id: u32,
    loaded_texture_buffers: [u32; 4],
    next_buffer_replace: u32,

    depth_buffers: [(DmaBuf, usize); 2],
    depth_buffer_idx: usize,

    cmd: CommandBuffer,
}

impl Gl {
    pub fn new() -> std::io::Result<Self> {
        let common = GlCommon::new()?;

        //SAFETY: the device tree must be correct
        let rasterizer = Rc::new(RefCell::new(unsafe {
            Rasterizer::new(Uio::open_named("rasterizer")?)?
        }));

        let alloc = Userdma::open()?;
        let cmd = CommandBuffer::new(rasterizer.clone(), &alloc)?;

        let z_size = common.width() * common.height() * 2;
        let z_size = (z_size + 4095) / 4096 * 4096;

        let s = Self {
            common,
            rasterizer,

            next_texture_buffer_id: 1,
            loaded_texture_buffers: [0, 0, 0, 0],
            next_buffer_replace: 0,

            depth_buffers: [
                alloc.alloc_buf(z_size)?,
                alloc.alloc_buf(z_size)?,
            ],
            depth_buffer_idx: 0,

            cmd,
        };

        unsafe {
            s.rasterizer.borrow_mut().set_buffers(
                s.common.frame_buffers[s.common.frame_buffer_idx].1,
                s.depth_buffers[s.depth_buffer_idx].1,
            );
        }

        Ok(s)
    }

    pub fn perf_counters(&self) -> crate::hal::PerfCounters {
        self.rasterizer.borrow_mut().perf_counters()
    }

    pub fn width(&self) -> usize {
        self.common.width()
    }

    pub fn height(&self) -> usize {
        self.common.height()
    }

    pub fn set_view_matrix(&mut self, view: Mat4) {
        self.common.set_view_matrix(view);
    }

    pub fn set_projection_matrix(&mut self, projection: Mat4) {
        self.common.set_projection_matrix(projection)
    }

    pub fn set_model_matrix(&mut self, model: Mat4) {
        self.common.set_model_matrix(model);
    }

    pub fn set_cull_mode(&mut self, mode: CullMode) {
        self.common.set_cull_mode(mode);
    }

    pub fn set_front_face(&mut self, face: FrontFace) {
        self.common.set_front_face(face);
    }

    pub fn create_texture_buffer(&mut self) -> TextureBuffer {
        let id = self.next_texture_buffer_id;
        self.next_texture_buffer_id += 1;
        TextureBuffer::new(id)
    }

    pub async fn begin_frame(&mut self) {
        unsafe {
            self.rasterizer.borrow_mut().set_buffers(
                self.common.frame_buffers[self.common.frame_buffer_idx].1,
                self.depth_buffers[self.depth_buffer_idx].1,
            );
        }

        //Wait for current frame/depth buffer clearing to finish
        self.cmd.wait_clear_idle().await;
        self.depth_buffer_idx = (self.depth_buffer_idx + 1) % self.depth_buffers.len();
        {
            let db = &self.depth_buffers[self.depth_buffer_idx];
            self.cmd.clear_buffer(db.1 as u32 >> 7, db.0.size() as u32 / 8, 0).await;
        }
    }

    pub async fn end_frame(&mut self) {
        //GlCommon::end_frame() displays the current buffer and advances the pointer
        let next_fb_idx = (self.common.frame_buffer_idx + 1) % self.common.frame_buffers.len();
        {
            let fb = &self.common.frame_buffers[next_fb_idx];
            self.cmd.clear_buffer(fb.1 as u32 >> 7, fb.0.size() as u32 / 8, 0xFFFFFF).await;
        }
        //Finish drawing
        self.cmd.wait_idle().await;
        self.cmd.flush().await;
        self.rasterizer.borrow_mut().wait_cmd().await;

        //overlap display with buffer clearing
        self.common.end_frame().await;
    }

    pub async fn draw_gouraud(
        &mut self,
        vertex_buffer: &[GouraudVertex],
        index_buffer: &[u16],
    ) {
        for v in self.common.transform_gouraud(vertex_buffer, index_buffer) {
            self.cmd.draw_triangle(
                None,
                v[0],
                v[1],
                v[2],
            ).await;
        }
    }

    pub async fn draw_texture(
        &mut self,
        texture_buffer: &TextureBuffer,
        vertex_buffer: &[TextureVertex],
        index_buffer: &[u16],
    ) {
        assert_eq!(index_buffer.len() % 3, 0);
        let (buffer, load) = match self.loaded_texture_buffers.iter().position(|v| *v == texture_buffer.id) {
            None => {
                let idx = self.next_buffer_replace;
                self.next_buffer_replace = (idx + 1) % 4;
                (idx as u8, true)
            },
            Some(x) => (x as u8, texture_buffer.dirty.get()),
        };
        if load {
            const QUADRANT_SIZE: usize = 64 * 64 * 3;
            self.cmd.load_texture(buffer, 0, 63, 0, 63, &texture_buffer.data[..QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 0, 63, 64, 127, &texture_buffer.data[QUADRANT_SIZE..2 * QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 64, 127, 0, 63, &texture_buffer.data[2 * QUADRANT_SIZE.. 3 * QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 64, 127, 64, 127, &texture_buffer.data[3 * QUADRANT_SIZE..]).await;
            texture_buffer.dirty.set(false);
        }

        for v in self.common.transform_texture(vertex_buffer, index_buffer) {
            self.cmd.draw_triangle(
                Some(buffer),
                v[0],
                v[1],
                v[2],
            ).await;
        }
    }
}