use std::ops::Deref;

pub mod display_controller;
pub mod dma_buf;
pub mod rasterizer;
pub mod uio;

pub use display_controller::DisplayController;
pub use dma_buf::DmaBuf;
pub use rasterizer::Rasterizer;
pub use uio::Uio;

pub struct MemoryMap {
    ptr: *mut libc::c_void,
    size: usize,
}

impl MemoryMap {
    pub fn size(&self) -> usize {
        self.size
    }
}

impl Deref for MemoryMap {
    type Target = *mut libc::c_void;

    fn deref(&self) -> &Self::Target {
        &self.ptr
    }
}

impl Drop for MemoryMap {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.ptr, self.size);
        }
    }
}

//SAFETY: caller's responsibility
unsafe impl Send for MemoryMap {}
unsafe impl Sync for MemoryMap {}
