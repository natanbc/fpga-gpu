use std::ptr::slice_from_raw_parts_mut;
use glam::Mat4;
use crate::gl::common::{GlCommon, ScreenVertex};

use crate::gl::common::{
    GouraudVertex,
    TextureVertex,
    CullMode,
    FrontFace
};
use crate::hal::MemoryMap;

pub struct TextureBuffer {
    data: Vec<u8>,
}

impl TextureBuffer {
    fn new() -> Self {
        Self {
            data: vec![0u8; 128 * 128 * 3],
        }
    }

    pub fn load(&mut self, rgb: &[u8]) {
        assert_eq!(rgb.len(), 128 * 128 * 3);
        self.data.copy_from_slice(rgb);
    }
}

fn draw_pixel(
    frame_buffer: &mut [u8],
    depth_buffer: &mut Vec<u16>,
    trig: [ScreenVertex; 3],
    color_map: impl Fn(u8, u8, u8) -> (u8, u8, u8),
    pos: usize,
    ws: [u32; 3],
) {
    macro_rules! interp {
        ($field:ident, $itype:ty, $rtype:ty) => {
            {
                let mut sum: $itype = 1 << 23;
                for i in 0..3 {
                    sum += trig[i].$field as $itype * ws[i] as $itype;
                }
                (sum >> 24) as $rtype
            }
        };
    }
    let z = interp!(z, u64, u16);
    if z <= depth_buffer[pos] {
        return;
    }
    depth_buffer[pos] = z;
    let (r, g, b) = color_map(
        interp!(r_s, u32, u8),
        interp!(g_t, u32, u8),
        interp!(b, u32, u8),
    );
    frame_buffer[pos * 3 + 0] = b;
    frame_buffer[pos * 3 + 1] = g;
    frame_buffer[pos * 3 + 2] = r;
}

fn draw(
    frame_buffer: &mut [u8],
    depth_buffer: &mut Vec<u16>,
    width: usize,
    trig: [ScreenVertex; 3],
    color_map: impl Fn(u8, u8, u8) -> (u8, u8, u8)
) {
    fn orient2d(a: [u16; 2], b: [u16; 2], c: [u16; 2]) -> i32 {
        (b[0] as i32 - a[0] as i32) * (c[1] as i32 - a[1] as i32) -
            (b[1] as i32 - a[1] as i32) * (c[0] as i32 - a[0] as i32)
    }

    let area = orient2d([trig[0].x, trig[0].y], [trig[1].x, trig[1].y], [trig[2].x, trig[2].y]);
    if area <= 0 {
        return;
    }
    let area_recip = (0xFFFFFF / area) as u32;

    let xs = trig.map(|v| v.x);
    let min_x = *xs.iter().min().unwrap();
    let max_x = *xs.iter().max().unwrap();
    let ys = trig.map(|v| v.y);
    let min_y = *ys.iter().min().unwrap();
    let max_y = *ys.iter().max().unwrap();

    let a01 = trig[0].y as i32 - trig[1].y as i32;
    let a12 = trig[1].y as i32 - trig[2].y as i32;
    let a20 = trig[2].y as i32 - trig[0].y as i32;
    let b01 = trig[1].x as i32 - trig[0].x as i32;
    let b12 = trig[2].x as i32 - trig[1].x as i32;
    let b20 = trig[0].x as i32 - trig[2].x as i32;

    let p = [min_x, min_y];
    let mut w0_row = orient2d([trig[1].x, trig[1].y], [trig[2].x, trig[2].y], p);
    let mut w1_row = orient2d([trig[2].x, trig[2].y], [trig[0].x, trig[0].y], p);
    let mut w2_row = orient2d([trig[0].x, trig[0].y], [trig[1].x, trig[1].y], p);

    for y in min_y..=max_y {
        let mut w0 = w0_row;
        let mut w1 = w1_row;
        let mut w2 = w2_row;
        for x in min_x..=max_x {
            if w0 >= 0 && w1 >= 0 && w2 >= 0 {
                draw_pixel(
                    frame_buffer,
                    depth_buffer,
                    trig,
                    &color_map,
                    y as usize * width + x as usize,
                    [w0 as u32 * area_recip, w1 as u32 * area_recip, w2 as u32 * area_recip],
                );
            }
            w0 += a12;
            w1 += a20;
            w2 += a01;
        }
        w0_row += b12;
        w1_row += b20;
        w2_row += b01;
    }
}

pub struct Gl {
    common: GlCommon,
    depth_buffer: Vec<u16>,
    fb_map: Option<MemoryMap>,
}

impl Gl {
    pub fn new() -> std::io::Result<Self> {
        let common = GlCommon::new()?;
        let z_count = common.width() * common.height();

        Ok(Self {
            common,
            depth_buffer: vec![0; z_count],
            fb_map: None,
        })
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
        TextureBuffer::new()
    }

    pub async fn begin_frame(&mut self) {
        self.common.begin_frame();
        self.depth_buffer.fill(0);
        let fb = &mut self.common.frame_buffers[self.common.frame_buffer_idx].0;
        fb.sync_start();
        self.fb_map = Some(fb.map().expect("Failed to map frame buffer"));
    }

    pub async fn end_frame(&mut self) {
        self.fb_map = None;
        self.common.frame_buffers[self.common.frame_buffer_idx].0.sync_end();
        self.common.end_frame().await;
    }

    pub async fn draw_gouraud(
        &mut self,
        vertex_buffer: &[GouraudVertex],
        index_buffer: &[u16],
    ) {
        let frame_buffer = **self.fb_map.as_ref().unwrap();
        let frame_buffer = unsafe {
            &mut *slice_from_raw_parts_mut(frame_buffer as *mut u8, self.width() * self.height() * 3)
        };
        let w = self.width();
        for v in self.common.transform_gouraud(vertex_buffer, index_buffer) {
            draw(frame_buffer, &mut self.depth_buffer, w, v, |a, b, c| (a, b, c));
        }
    }

    pub async fn draw_texture(
        &mut self,
        texture_buffer: &TextureBuffer,
        vertex_buffer: &[TextureVertex],
        index_buffer: &[u16],
    ) {
        assert_eq!(index_buffer.len() % 3, 0);
        let frame_buffer = **self.fb_map.as_ref().unwrap();
        let frame_buffer = unsafe {
            &mut *slice_from_raw_parts_mut(frame_buffer as *mut u8, self.width() * self.height() * 3)
        };
        let w = self.width();
        for v in self.common.transform_texture(vertex_buffer, index_buffer) {
            draw(frame_buffer, &mut self.depth_buffer, w, v, |s, t, _| {
                let idx = ((s as usize >> 1) * 128 + (t as usize >> 1)) * 3;
                let rgb = &texture_buffer.data[idx..idx+3];
                (rgb[0], rgb[1], rgb[2])
            })
        }
    }
}