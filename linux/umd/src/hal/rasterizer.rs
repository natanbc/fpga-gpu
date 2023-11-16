use std::ptr::{addr_of, addr_of_mut};
use std::sync::Arc;
use tokio::task::JoinHandle;
use crate::hal::{MemoryMap, Uio};

#[repr(C)]
#[derive(Copy, Clone, Debug)]
pub struct Stalls {
    pub walker_searching: u32,
    pub depth_load_addr: u32,
    pub depth_fifo: u32,
    pub depth_load_data: u32,
    pub depth_store_addr: u32,
    pub depth_store_data: u32,
    pub pixel_store: u32,
}

impl Stalls {
    pub fn diff(&self, previous: Self) -> Self {
        Self {
            walker_searching: self.walker_searching.wrapping_sub(previous.walker_searching),
            depth_load_addr: self.depth_load_addr.wrapping_sub(previous.depth_load_addr),
            depth_fifo: self.depth_fifo.wrapping_sub(previous.depth_fifo),
            depth_load_data: self.depth_load_data.wrapping_sub(previous.depth_load_data),
            depth_store_addr: self.depth_store_addr.wrapping_sub(previous.depth_store_addr),
            depth_store_data: self.depth_store_data.wrapping_sub(previous.depth_store_data),
            pixel_store: self.pixel_store.wrapping_sub(previous.pixel_store),
        }
    }
}

#[repr(C)]
#[derive(Copy, Clone, Debug)]
pub struct PerfCounters {
    pub busy_cycles: u32,
    pub stalls: Stalls,
    pub fifo_depth: [u32; 9],
}

impl PerfCounters {
    pub fn diff(&self, previous: Self) -> Self {
        let mut depths = [0; 9];
        for (i, v) in depths.iter_mut().enumerate() {
            *v = self.fifo_depth[i].wrapping_sub(previous.fifo_depth[i]);
        }
        Self {
            busy_cycles: self.busy_cycles.wrapping_sub(previous.busy_cycles),
            stalls: self.stalls.diff(previous.stalls),
            fifo_depth: depths,
        }
    }
}

#[repr(C)]
struct RasterizerRegisters {
    irq_status: u32,
    irq_mask: u32,
    fb_base: u32,
    z_base: u32,
    idle: u32,
    cmd_addr_64: u32,
    cmd_words: u32,
    cmd_ctrl: u32,
    cmd_dma_idle: u32,
    cmd_idle: u32,
    perf_counters: PerfCounters,
}

pub struct Rasterizer {
    map: Arc<MemoryMap>,
    cmd_done: tokio::sync::watch::Receiver<()>,
    cmd_dma_done: tokio::sync::watch::Receiver<()>,
    irq_handle: JoinHandle<()>,
}

impl Rasterizer {
    pub unsafe fn new(mut uio: Uio) -> std::io::Result<Self> {
        let map = Arc::new(uio.map(0)?);

        let (cmd_done_tx, cmd_done_rx) = tokio::sync::watch::channel(());
        let (cmd_dma_done_tx, cmd_dma_done_rx) = tokio::sync::watch::channel(());
        let irq_handle = {
            let map = map.clone();
            tokio::spawn(async move {
                let _ = cmd_done_tx.send(());
                let _ = cmd_dma_done_tx.send(());
                loop {
                    uio.enable_irq();
                    uio.wait_irq().await.expect("wait_irq failed");
                    let status = unsafe {
                        let regs = **map as *mut RasterizerRegisters;
                        let status = addr_of!((*regs).irq_status).read_volatile();
                        addr_of_mut!((*regs).irq_status).write_volatile(status);
                        status
                    };
                    if (status & 0b01) != 0 {
                        if cmd_done_tx.send(()).is_err() {
                            break;
                        }
                    }
                    if (status & 0b10) != 0 {
                        if cmd_dma_done_tx.send(()).is_err() {
                            break;
                        }
                    }
                }
            })
        };

        let mut s = Self {
            map,
            cmd_done: cmd_done_rx,
            cmd_dma_done: cmd_dma_done_rx,
            irq_handle,
        };

        unsafe {
            //enable command done/command dma done irqs
            addr_of_mut!((*s.regs_mut()).irq_mask).write_volatile(0b11);
        }
        Ok(s)
    }

    pub fn perf_counters(&self) -> PerfCounters {
        unsafe {
            addr_of!((*self.regs()).perf_counters).read_volatile()
        }
    }

    pub async fn wait_cmd_dma(&mut self) {
        self.cmd_dma_done.changed().await.expect("Sender should never be dropped before receiver");
    }

    pub async fn wait_cmd(&mut self) {
        self.cmd_done.changed().await.expect("Sender should never be dropped before receiver");
    }

    pub unsafe fn submit_commands(&mut self, buffer: usize, words: usize) {
        let regs = **self.map as *mut RasterizerRegisters;
        assert_eq!(addr_of!((*regs).cmd_dma_idle).read_volatile(), 1);

        self.cmd_dma_done.borrow_and_update();
        self.cmd_done.borrow_and_update();

        addr_of_mut!((*regs).cmd_addr_64).write_volatile((buffer >> 6) as u32);
        addr_of_mut!((*regs).cmd_words).write_volatile(words as u32);
        addr_of_mut!((*regs).cmd_ctrl).write_volatile(
            addr_of!((*regs).cmd_ctrl).read_volatile() ^ 1
        );
    }

    pub unsafe fn set_buffers(&mut self, frame_buffer: usize, z_buffer: usize) {
        let regs = **self.map as *mut RasterizerRegisters;
        addr_of_mut!((*regs).fb_base).write_volatile(frame_buffer as u32);
        addr_of_mut!((*regs).z_base).write_volatile(z_buffer as u32);
    }

    fn regs(&self) -> *const RasterizerRegisters {
        **self.map as *const RasterizerRegisters
    }

    fn regs_mut(&mut self) -> *mut RasterizerRegisters {
        **self.map as *mut RasterizerRegisters
    }
}

impl Drop for Rasterizer {
    fn drop(&mut self) {
        self.irq_handle.abort();
    }
}

unsafe impl Send for Rasterizer {}
