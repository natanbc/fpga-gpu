use glam::{Mat4, Vec3, Vec4, Vec4Swizzles};
use mycelium_bitfield::Pack64;
use crate::hal::{DisplayController, DmaBuf, Uio, Userdma};

#[derive(Copy, Clone, Debug)]
pub struct GouraudVertex {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub r: f32,
    pub g: f32,
    pub b: f32,
}

#[derive(Copy, Clone, Debug)]
pub struct TextureVertex {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub s: f32,
    pub t: f32,
}

#[derive(Copy, Clone, Eq, PartialEq, Debug)]
pub enum CullMode {
    BackFace,
    FrontFace,
    None,
}

#[derive(Copy, Clone, Eq, PartialEq, Debug)]
pub enum FrontFace {
    Clockwise,
    CounterClockwise,
}

#[derive(Copy, Clone, Debug)]
pub(crate) struct ScreenVertex {
    pub(crate) x: u16,
    pub(crate) y: u16,
    pub(crate) z: u16,
    pub(crate) r_s: u8,
    pub(crate) g_t: u8,
    pub(crate) b: u8,
}

impl ScreenVertex {
    const X: Pack64 = Pack64::least_significant(11);
    const Y: Pack64 = Self::X.next(11);
    const Z: Pack64 = Self::Y.next(16);
    const R_S: Pack64 = Self::Z.next(8);
    const G_T: Pack64 = Self::R_S.next(8);
    const B: Pack64 = Self::G_T.next(8);

    pub fn pack(&self) -> (u32, u32) {
        let val = Pack64::pack_in(0)
            .pack(self.x as u64, &Self::X)
            .pack(self.y as u64, &Self::Y)
            .pack(self.z as u64, &Self::Z)
            .pack(self.r_s as u64, &Self::R_S)
            .pack(self.g_t as u64, &Self::G_T)
            .pack(self.b as u64, &Self::B)
            .bits();
        (val as u32, (val >> 32) as u32)
    }
}

pub(crate) struct GlCommon {
    dc: DisplayController,

    view: Mat4,
    projection: Mat4,
    projection_view: Mat4, /* precomputed projection * view */

    model: Mat4,

    scale_device: Vec3,

    pub(crate) frame_buffers: [(DmaBuf, usize); 3],
    pub(crate) frame_buffer_idx: usize,

    cull_mode: CullMode,
    front_face: FrontFace,
}

impl GlCommon {
    pub fn new() -> std::io::Result<Self> {
        //SAFETY: the device tree must be correct
        let dc = unsafe {
            DisplayController::new(Uio::open_named("display_controller")?)?
        };

        let width = dc.width() as usize;
        let height = dc.height() as usize;

        let alloc = Userdma::open()?;

        let fb_size = width * height * 3;
        let fb_size = (fb_size + 4095) / 4096 * 4096;

        let s = Self {
            dc,

            view: Mat4::IDENTITY,
            projection: Mat4::IDENTITY,
            projection_view: Mat4::IDENTITY,

            model: Mat4::IDENTITY,

            scale_device: Vec3::new((width - 1) as f32, (height - 1) as f32, 65535.0),

            frame_buffers: [
                alloc.alloc_buf(fb_size)?,
                alloc.alloc_buf(fb_size)?,
                alloc.alloc_buf(fb_size)?,
            ],
            frame_buffer_idx: 0,

            cull_mode: CullMode::BackFace,
            front_face: FrontFace::CounterClockwise,
        };

        Ok(s)
    }

    pub fn width(&self) -> usize {
        self.dc.width() as usize
    }

    pub fn height(&self) -> usize {
        self.dc.height() as usize
    }

    pub fn set_view_matrix(&mut self, view: Mat4) {
        self.view = view;
        self.projection_view = self.projection * view;
    }

    pub fn set_projection_matrix(&mut self, projection: Mat4) {
        self.projection = projection;
        self.projection_view = projection * self.view;
    }

    pub fn set_model_matrix(&mut self, model: Mat4) {
        self.model = model;
    }

    pub fn set_cull_mode(&mut self, mode: CullMode) {
        self.cull_mode = mode;
    }

    pub fn set_front_face(&mut self, face: FrontFace) {
        self.front_face = face;
    }

    pub fn begin_frame(&mut self) {
        let fb = &mut self.frame_buffers[self.frame_buffer_idx].0;
        let fb_map = fb.map().unwrap();
        fb.with_sync(|| {
            unsafe {
                libc::memset(*fb_map, 0xFF, fb_map.size());
            }
        });
    }

    pub async fn end_frame(&mut self) {
        //TODO: buffer_clearer async clearing
        unsafe {
            self.dc.draw_frame(self.frame_buffers[self.frame_buffer_idx].1);
        }
        self.dc.wait_end_of_frame().await;

        self.frame_buffer_idx = (self.frame_buffer_idx + 1) % self.frame_buffers.len();
    }

    pub fn transform_gouraud<'a>(
        &'a mut self,
        vertex_buffer: &'a [GouraudVertex],
        index_buffer: &'a [u16],
    ) -> impl Iterator<Item=[ScreenVertex; 3]> + 'a {
        assert_eq!(index_buffer.len() % 3, 0);
        let iter = TriangleIterator {
            vertices: vertex_buffer,
            indices: index_buffer,
            idx: 0,
        };
        iter.filter_map(|trig| {
            let mut vertices = [
                ScreenVertex {
                    x: 0,
                    y: 0,
                    z: 0,
                    r_s: 0,
                    g_t: 0,
                    b: 0,
                }; 3
            ];
            for i in 0..3 {
                let vertex = trig[i];
                let transformed = self.transform_vertex(vertex.x, vertex.y, vertex.z);
                vertices[i] = ScreenVertex {
                    x: transformed[0],
                    y: transformed[1],
                    z: transformed[2],
                    r_s: (vertex.r * 255.0) as u8,
                    g_t: (vertex.g * 255.0) as u8,
                    b: (vertex.b * 255.0) as u8,
                };
            }
            self.cull(vertices)
        })
    }

    pub fn transform_texture<'a>(
        &'a mut self,
        vertex_buffer: &'a [TextureVertex],
        index_buffer: &'a [u16],
    ) -> impl Iterator<Item=[ScreenVertex; 3]> + 'a {
        assert_eq!(index_buffer.len() % 3, 0);
        let iter = TriangleIterator {
            vertices: vertex_buffer,
            indices: index_buffer,
            idx: 0,
        };
        iter.filter_map(|trig| {
            let mut vertices = [
                ScreenVertex {
                    x: 0,
                    y: 0,
                    z: 0,
                    r_s: 0,
                    g_t: 0,
                    b: 0,
                }; 3
            ];
            for i in 0..3 {
                let vertex = trig[i];
                let transformed = self.transform_vertex(vertex.x, vertex.y, vertex.z);
                vertices[i] = ScreenVertex {
                    x: transformed[0],
                    y: transformed[1],
                    z: transformed[2],
                    r_s: ((1.0 - vertex.s) * 255.0) as u8,
                    g_t: (vertex.t * 255.0) as u8,
                    b: 0,
                };
            }
            self.cull(vertices)
        })
    }

    fn cull(&mut self, mut vertices: [ScreenVertex; 3]) -> Option<[ScreenVertex; 3]> {
        if self.front_face == FrontFace::CounterClockwise {
            let tmp = vertices[1];
            vertices[1] = vertices[2];
            vertices[2] = tmp;
        }

        match self.cull_mode {
            CullMode::BackFace => {
                //Back face culling is always done in hardware, other modes have to be done in
                //software.
            },
            CullMode::None|CullMode::FrontFace => {
                fn orient2d(a: ScreenVertex, b: ScreenVertex, c: ScreenVertex) -> i32 {
                    (b.x as i32 - a.x as i32) * (c.y as i32 - a.y as i32) -
                        (b.y as i32 - a.y as i32) * (c.x as i32 - a.x as i32)
                }

                let orientation = orient2d(vertices[0], vertices[1], vertices[2]);
                if self.cull_mode == CullMode::None && orientation < 0 {
                    //If culling is disabled but this triangle would be skipped during rasterization,
                    //change the winding order to draw it
                    let tmp = vertices[1];
                    vertices[1] = vertices[2];
                    vertices[2] = tmp;
                } else if self.cull_mode == CullMode::FrontFace && orientation >= 0 {
                    //If front face culling is enabled and this triangle is front facing, skip it.
                    return None;
                }
            }
        }

        return Some(vertices);
    }

    fn transform_vertex(&self, x: f32, y: f32, z: f32) -> [u16; 3] {
        let tv = self.projection_view * self.model * Vec4::new(x, y, z, 1.0);
        let tv = tv.xyz() / tv.w;

        let tv_0_1 = tv * Vec3::new(0.5, -0.5, -0.5) + 0.5;
        let tv_dev = tv_0_1 * self.scale_device;
        let res = tv_dev.as_uvec3().to_array();
        assert!(res[0] < self.dc.width() as u32);
        assert!(res[1] < self.dc.height() as u32);
        assert!(res[2] < 65536);
        [res[0] as u16, res[1] as u16, res[2] as u16]
    }
}

struct TriangleIterator<'a, T: Copy> {
    vertices: &'a [T],
    indices: &'a [u16],
    idx: usize,
}

impl<'a, T: Copy> Iterator for TriangleIterator<'a, T> {
    type Item = [T; 3];

    fn next(&mut self) -> Option<Self::Item> {
        if self.idx >= self.indices.len() {
            return None;
        }
        let res = [
            self.vertices[self.indices[self.idx + 0] as usize],
            self.vertices[self.indices[self.idx + 1] as usize],
            self.vertices[self.indices[self.idx + 2] as usize],
        ];
        self.idx += 3;
        return Some(res)
    }
}
