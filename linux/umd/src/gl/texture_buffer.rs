use std::cell::Cell;

pub struct TextureBuffer {
    pub(crate) id: u32,
    pub(crate) data: Vec<u8>,
    pub(crate) dirty: Cell<bool>,
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
    pub(crate) fn new(id: u32) -> Self {
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
