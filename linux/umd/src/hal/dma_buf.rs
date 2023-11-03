#![allow(unused)]

use std::io::{Result, Error};
use super::MemoryMap;

const DMA_BUF_IOCTL_SYNC: libc::c_int = 0x40086200;
const DMA_BUF_SET_NAME: libc::c_int = 0x40086201;
const DMA_BUF_SET_NAME_A: libc::c_int = 0x40046201;
const DMA_BUF_SET_NAME_B: libc::c_int = 0x40086201;
const DMA_BUF_IOCTL_EXPORT_SYNC_FILE: libc::c_int = 0xC0086202u32 as i32;
const DMA_BUF_IOCTL_IMPORT_SYNC_FILE: libc::c_int = 0x40086203;

const DMA_BUF_SYNC_READ: u64 = 0x01;
const DMA_BUF_SYNC_WRITE: u64 = 0x02;
const DMA_BUF_SYNC_RW: u64 = DMA_BUF_SYNC_READ | DMA_BUF_SYNC_WRITE;
const DMA_BUF_SYNC_START: u64 = 0;
const DMA_BUF_SYNC_END: u64 = 0x04;
const DMA_BUF_SYNC_VALID_FLAGS_MASK: u64 = DMA_BUF_SYNC_RW | DMA_BUF_SYNC_END;
const DMA_BUF_NAME_LEN: usize = 32;

#[repr(C)]
struct DmaBufSync {
    flags: u64,
}

#[repr(C)]
struct DmaBufExportSyncFile {
    flags: u32,
    fd: i32,
}

#[repr(C)]
struct DmaBufImportSyncFile {
    flags: u32,
    fd: i32,
}

pub struct DmaBuf {
    pub(crate) fd: libc::c_int,
    size: usize,
}

impl DmaBuf {
    pub unsafe fn from_fd(fd: libc::c_int, size: usize) -> Self {
        Self {
            fd,
            size,
        }
    }

    pub fn map(&mut self) -> Result<MemoryMap> {
        let map = unsafe {
            libc::mmap(
                core::ptr::null_mut(),
                self.size,
                libc::PROT_READ|libc::PROT_WRITE,
                libc::MAP_SHARED,
                self.fd,
                0
            )
        };
        if map == libc::MAP_FAILED {
            return Err(Error::last_os_error());
        }
        Ok(MemoryMap {
            ptr: map,
            size: self.size,
        })
    }

    pub fn with_sync<T>(&mut self, cb: impl FnOnce() -> T) -> T {
        self.sync_start();
        let res = cb();
        self.sync_end();
        res
    }

    pub fn sync_start(&mut self) {
        self.sync(DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW)
    }

    pub fn sync_end(&mut self) {
        self.sync(DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW)
    }

    fn sync(&mut self, flags: u64) {
        let sync = DmaBufSync {
            flags,
        };
        let res = unsafe {
            libc::ioctl(self.fd, DMA_BUF_IOCTL_SYNC, &sync)
        };
        if res != 0 {
            panic!("DMA_BUF_IOCTL_SYNC failed: {:?}", Error::last_os_error());
        }
    }
}

unsafe impl Send for DmaBuf {}

impl Drop for DmaBuf {
    fn drop(&mut self) {
        unsafe {
            libc::close(self.fd);
        }
    }
}