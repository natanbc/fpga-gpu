use std::fs::{File, OpenOptions};
use std::io::{Result, Error};
use std::os::fd::AsRawFd;

#[repr(C)]
struct UserdmaBufCreateData {
    size: u32,
    phys_addr: u32,
}

#[repr(C)]
struct UserdmaPhysAddrData {
    fd: i32,
    phys_addr: u32,
}

const USERDMA_IOCTL_PRINT: i32 = 0x80047501u32 as i32;
const USERDMA_IOCTL_ALLOC: i32 = 0xc0087502u32 as i32;
const USERDMA_IOCTL_GET_PHYS: i32 = 0xc0087503u32 as i32;

pub struct Userdma {
    dev_file: File,
}

impl Userdma {
    pub fn open() -> Result<Self> {
        OpenOptions::new().read(true).write(true).open("/dev/userdma").map(|f| {
            Self {
                dev_file: f,
            }
        })
    }

    pub fn alloc_buf(&self, size: usize) -> Result<(super::DmaBuf, usize)> {
        let mut data = UserdmaBufCreateData {
            size: size as u32,
            phys_addr: 0,
        };
        let fd = unsafe {
            libc::ioctl(self.dev_file.as_raw_fd(), USERDMA_IOCTL_ALLOC, &mut data)
        };
        if fd < 0 {
            return Err(Error::last_os_error());
        }
        let buf = unsafe {
            super::DmaBuf::from_fd(fd, size)
        };
        Ok((buf, data.phys_addr as usize))
    }

    pub fn print_buf(&self, buf: &super::DmaBuf) -> Result<()> {
        let res = unsafe {
            libc::ioctl(self.dev_file.as_raw_fd(), USERDMA_IOCTL_PRINT, buf.fd)
        };
        if res != 0 {
            return Err(Error::last_os_error());
        }
        Ok(())
    }

    pub fn get_phys(&self, buf: &super::DmaBuf) -> Result<usize> {
        let mut data = UserdmaPhysAddrData {
            fd: buf.fd,
            phys_addr: 0,
        };
        let fd = unsafe {
            libc::ioctl(self.dev_file.as_raw_fd(), USERDMA_IOCTL_GET_PHYS, &mut data)
        };
        if fd < 0 {
            return Err(Error::last_os_error());
        }
        Ok(data.phys_addr as usize)
    }
}
