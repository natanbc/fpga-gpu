use std::fs::OpenOptions;
use std::io::Error;
use std::os::fd::{FromRawFd, IntoRawFd};
use std::str::FromStr;
use tokio::fs::File;
use tokio::io::AsyncReadExt;
use super::MemoryMap;

pub struct Uio {
    file: File,
    fd: libc::c_int,
    number: u32,
}

// Synchronous IO done in cases where it won't ever block
impl Uio {
    pub fn open(number: u32) -> std::io::Result<Self> {
        let file = OpenOptions::new().read(true).write(true).open(format!("/dev/uio{number}"))?;
        let fd = file.into_raw_fd();
        Ok(Self {
            file: unsafe {
                File::from_raw_fd(fd)
            },
            fd,
            number,
        })
    }

    fn find_number(name: &str) -> std::io::Result<u32> {
        for entry in std::fs::read_dir("/sys/class/uio")? {
            let entry = entry?;
            let mut p = entry.path();
            p.push("name");
            let device_name = std::fs::read_to_string(p)?;
            let device_name = device_name.trim();
            if name == device_name {
                let uio_name = entry.file_name().into_string().expect("Invalid UIO name");
                assert!(uio_name.starts_with("uio"));
                return Ok(u32::from_str(&uio_name[3..]).expect("Invalid UIO number"));
            }
        }
        Err(Error::from_raw_os_error(libc::ENODEV))
    }

    pub fn open_named(name: &str) -> std::io::Result<Self> {
        Self::open(Self::find_number(name)?)
    }

    pub unsafe fn map(&mut self, mapping: u32) -> std::io::Result<MemoryMap> {
        let size = std::fs::read_to_string(format!(
            "/sys/class/uio/uio{}/maps/map{}/size",
            self.number, mapping
        ))?;
        let size = size.trim();
        let size = usize::from_str_radix(&size[2..], 16).expect("Invalid size");

        let res = libc::mmap(
            core::ptr::null_mut(),
            size,
            libc::PROT_READ|libc::PROT_WRITE,
            libc::MAP_SHARED,
            self.fd,
            (mapping * 4096).into(),
        );
        if res == libc::MAP_FAILED {
            return Err(Error::last_os_error());
        }

        Ok(MemoryMap {
            ptr: res,
            size,
        })
    }

    fn write(&mut self, v: u32) {
        let buf = v.to_ne_bytes();
        let res = unsafe {
            libc::write(self.fd, buf.as_ptr() as *const libc::c_void, 4)
        };
        if res != 4 {
            panic!("UIO write failed: {res} / {}", Error::last_os_error());
        }
    }

    pub fn enable_irq(&mut self) {
        self.write(1);
    }

    pub fn disable_irq(&mut self) {
        self.write(0);
    }

    pub async fn wait_irq(&mut self) -> std::io::Result<()> {
        self.file.read_u32_le().await?;
        Ok(())
    }
}

