use std::ptr::{addr_of, addr_of_mut};
use std::sync::Arc;
use tokio::task::JoinHandle;
use crate::hal::uio::Uio;
use crate::hal::MemoryMap;

#[repr(C)]
struct DisplayControllerRegisters {
    irq_status: u32,
    irq_mask: u32,
    width: u32,
    height: u32,
    page_addr: u32,
    words: u32,
    ctrl: u32,
}

pub struct DisplayController {
    map: Arc<MemoryMap>,
    draw_done_irq: tokio::sync::watch::Receiver<()>,
    irq_handle: JoinHandle<()>
}

impl DisplayController {
    pub unsafe fn new(mut uio: Uio) -> std::io::Result<Self> {
        let map = Arc::new(uio.map(0)?);

        let (draw_done_tx, draw_done_rx) = tokio::sync::watch::channel(());
        let irq_handle = {
            let map = map.clone();
            tokio::spawn(async move {
                loop {
                    uio.enable_irq();
                    uio.wait_irq().await.expect("wait_irq failed");
                    unsafe {
                        let regs = **map as *mut DisplayControllerRegisters;
                        let status = addr_of!((*regs).irq_status).read_volatile();
                        addr_of_mut!((*regs).irq_status).write_volatile(status);
                    }
                    if draw_done_tx.send(()).is_err() {
                        break;
                    }
                }
            })
        };

        let mut s = Self {
            map,
            draw_done_irq: draw_done_rx,
            irq_handle,
        };

        let size = s.width() * s.height() * 3;
        assert_eq!(size % 8, 0);
        unsafe {
            //disable
            addr_of_mut!((*s.regs_mut()).ctrl).write_volatile(0b0);
            //set buffer size
            addr_of_mut!((*s.regs_mut()).words).write_volatile(size / 8);
            //enable draw done irq only
            addr_of_mut!((*s.regs_mut()).irq_mask).write_volatile(0b10);
        }
        Ok(s)
    }

    pub fn width(&self) -> u32 {
        unsafe {
            addr_of!((*self.regs()).width).read()
        }
    }

    pub fn height(&self) -> u32 {
        unsafe {
            addr_of!((*self.regs()).height).read()
        }
    }

    pub async fn wait_end_of_frame(&mut self) {
        self.draw_done_irq.changed().await.expect("Sender should never be dropped before receiver");
    }

    //TODO: better buffer abstraction instead of passing physical addresses
    pub unsafe fn draw_frame(&mut self, addr: usize) {
        assert_eq!(addr & 0xFFF, 0, "Address must be page alingned");
        let aligned = (addr >> 12) as u32;
        addr_of_mut!((*self.regs_mut()).page_addr).write_volatile(aligned);
        addr_of_mut!((*self.regs_mut()).ctrl).write_volatile(1);
    }

    fn regs(&self) -> *const DisplayControllerRegisters {
        **self.map as *const DisplayControllerRegisters
    }

    fn regs_mut(&mut self) -> *mut DisplayControllerRegisters {
        **self.map as *mut DisplayControllerRegisters
    }
}

impl Drop for DisplayController {
    fn drop(&mut self) {
        self.irq_handle.abort();

        unsafe {
            addr_of_mut!((*self.regs_mut()).ctrl).write_volatile(0);
        }
    }
}

unsafe impl Send for DisplayController {}
