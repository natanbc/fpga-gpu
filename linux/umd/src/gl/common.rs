use glam::{Mat4, Vec2, Vec3, Vec4, Vec4Swizzles};
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
struct ClipVertex {
    pos: [f32; 4],
    r_s: f32,
    g_t: f32,
    b: f32,
}

const CLIP_NEG_X: u8 = 0x01;
const CLIP_POS_X: u8 = 0x02;
const CLIP_NEG_Y: u8 = 0x04;
const CLIP_POS_Y: u8 = 0x08;
const CLIP_NEG_Z: u8 = 0x10;
const CLIP_POS_Z: u8 = 0x20;

impl ClipVertex {
    fn classify(&self) -> u8 {
        let mut code = 0;
        if self.pos[0] < -self.pos[3] {
            code |= CLIP_NEG_X;
        }
        if self.pos[0] > self.pos[3] {
            code |= CLIP_POS_X;
        }
        if self.pos[1] < -self.pos[3] {
            code |= CLIP_NEG_Y;
        }
        if self.pos[1] > self.pos[3] {
            code |= CLIP_POS_Y;
        }
        if self.pos[2] < -self.pos[3] {
            code |= CLIP_NEG_Z;
        }
        if self.pos[2] > self.pos[3] {
            code |= CLIP_POS_Z;
        }
        code
    }
}

fn plane_to_clip_params(plane: u8) -> (f32, usize) {
    match plane {
        CLIP_NEG_X => (-1.0, 0),
        CLIP_POS_X => (1.0, 0),
        CLIP_NEG_Y => (-1.0, 1),
        CLIP_POS_Y => (1.0, 1),
        CLIP_NEG_Z => (-1.0, 2),
        CLIP_POS_Z => (1.0, 2),
        _ => unreachable!(),
    }
}

fn clip_against_plane(src: &Vec<ClipVertex>, plane: u8, dst: &mut Vec<ClipVertex>) {
    let (sign, index) = plane_to_clip_params(plane);

    for (i, vertex) in src.iter().enumerate() {
        let v0 = if i == 0 {
            src.last().unwrap()
        } else {
            &src[i - 1]
        };
        let v1 = vertex;

        let p0 = v0.pos[index] * sign;
        let p1 = v1.pos[index] * sign;
        let w0 = v0.pos[3];
        let w1 = v1.pos[3];

        if p0 < w0 {
            dst.push(*v0);
        }

        if (p0 < w0 && p1 >= w1) || (p0 >= w0 && p1 < w1) {
            let denom = -p0 + p1 + w0 - w1;
            if denom.abs() > 0.001 {
                let t = (-p0 + w0) / denom;
                dst.push(ClipVertex {
                    pos: (Vec4::from_array(v0.pos) * (1.0 - t) + Vec4::from_array(v1.pos) * t).to_array(),
                    r_s: v0.r_s * (1.0 - t) + v1.r_s * t,
                    g_t: v0.g_t * (1.0 - t) + v1.g_t * t,
                    b: v0.b * (1.0 - t) + v1.b * t,
                })
            }
        }
    }
}

fn clip(vertices: [ClipVertex; 3]) -> impl Iterator<Item=[ClipVertex; 3]> {
    let vec = 'a: {
        let c0 = vertices[0].classify();
        let c1 = vertices[1].classify();
        let c2 = vertices[2].classify();
        if (c0 & c1 & c2) != 0 {
            break 'a vec![];
        }
        if (c0 | c1 | c2) == 0 {
            break 'a vec![vertices[0], vertices[1], vertices[2]];
        }

        let mut vertices_in = vertices.into_iter().collect::<Vec<_>>();
        let mut vertices_out = vec![];
        for plane in [CLIP_NEG_X, CLIP_POS_X, CLIP_NEG_Y, CLIP_POS_Y, CLIP_NEG_Z, CLIP_POS_Z] {
            vertices_out.clear();
            clip_against_plane(&vertices_in, plane, &mut vertices_out);
            let tmp = vertices_out;
            vertices_out = vertices_in;
            vertices_in = tmp;
        }
        if vertices_out.len() > 0 {
            if (vertices_out[0].classify() & vertices_out[1].classify() & vertices_out[2].classify()) != 0 {
                break 'a vec![];
            }
        }
        vertices_out
    };
    let first = vec.first().map(|v| *v).unwrap_or(ClipVertex {
        pos: [0.0, 0.0, 0.0, 0.0],
        r_s: 0.0,
        g_t: 0.0,
        b: 0.0,
    });
    (2..=vec.len()).map(move |i| [first, vec[i - 2], vec[i - 1]])
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

            cull_mode: CullMode::None,
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

    pub async fn end_frame(&mut self, draw: bool) {
        if draw {
            unsafe {
                self.dc.draw_frame(self.frame_buffers[self.frame_buffer_idx].1);
            }
            self.dc.wait_end_of_frame().await;
        }

        self.frame_buffer_idx = (self.frame_buffer_idx + 1) % self.frame_buffers.len();
    }

    #[inline(always)]
    fn transform<'a, T: Copy>(
        &'a mut self,
        vertex_buffer: &'a [T],
        index_buffer: &'a [u16],
        get_pos: impl Fn(T) -> [f32; 3] + 'a,
        clip_attr_map: impl Fn(T) -> [f32; 3] + 'a,
        screen_attr_map: impl Fn(f32, f32, f32) -> [u8; 3] + 'a,
    ) -> impl Iterator<Item=[ScreenVertex; 3]> + 'a {
        assert_eq!(index_buffer.len() % 3, 0);
        let iter = TriangleIterator {
            vertices: vertex_buffer,
            indices: index_buffer,
            idx: 0,
        };
        let to_clip = {
            let pvm = self.projection_view * self.model;
            move |x, y, z| {
                (pvm * Vec4::new(x, y, z, 1.0)).to_array()
            }
        };
        let to_screen = {
            let scale_device = self.scale_device;
            let w = self.dc.width();
            let h = self.dc.height();
            move |trig| {
                let tv = Vec4::from_array(trig);
                let tv = tv.xyz() / tv.w;

                let tv_0_1 = tv * Vec3::new(0.5, -0.5, -0.5) + 0.5;
                let tv_dev = tv_0_1 * scale_device;
                let res = tv_dev.as_uvec3().to_array();
                assert!(res[0] < w as u32);
                assert!(res[1] < h as u32);
                assert!(res[2] < 65536);
                [res[0] as u16, res[1] as u16, res[2] as u16]
            }
        };
        iter
            .map(move |trig| {
                let mut vertices = [
                    ClipVertex {
                        pos: [0.0, 0.0, 0.0, 0.0],
                        r_s: 0.0,
                        g_t: 0.0,
                        b: 0.0,
                    }; 3
                ];
                for i in 0..3 {
                    let [x, y, z] = get_pos(trig[i]);
                    let [r_s, g_t, b] = clip_attr_map(trig[i]);
                    let transformed = to_clip(x, y, z);
                    vertices[i] = ClipVertex {
                        pos: transformed,
                        r_s,
                        g_t,
                        b,
                    };
                }
                vertices
            })
            .filter_map(|trig| {
                self.cull(trig)
            })
            .flat_map(move |trig| {
                clip(trig)
            })
            .map(move |trig| {
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
                    let p = to_screen(vertex.pos);
                    let [r_s, g_t, b] = screen_attr_map(vertex.r_s, vertex.g_t, vertex.b);
                    vertices[i] = ScreenVertex {
                        x: p[0],
                        y: p[1],
                        z: p[2],
                        r_s,
                        g_t,
                        b,
                    };
                }
                vertices
            })
    }

    pub fn transform_gouraud<'a>(
        &'a mut self,
        vertex_buffer: &'a [GouraudVertex],
        index_buffer: &'a [u16],
    ) -> impl Iterator<Item=[ScreenVertex; 3]> + 'a {
        #[inline(always)]
        fn to_u8(v: f32) -> u8 {
            (v * 255.0) as u8
        }

        self.transform(
            vertex_buffer,
            index_buffer,
            |v| [v.x, v.y, v.z],
            |v| [v.r, v.g, v.b],
            |r, g, b| [to_u8(r), to_u8(g), to_u8(b)],
        )
    }

    pub fn transform_texture<'a>(
        &'a mut self,
        vertex_buffer: &'a [TextureVertex],
        index_buffer: &'a [u16],
    ) -> impl Iterator<Item=[ScreenVertex; 3]> + 'a {
        #[inline(always)]
        fn to_u8(v: f32) -> u8 {
            (v * 255.0) as u8
        }

        self.transform(
            vertex_buffer,
            index_buffer,
            |v| [v.x, v.y, v.z],
            |v| [v.s, v.t, 0.0],
            |s, t, _| [to_u8(1.0 - s), to_u8(t), 0],
        )
    }

    fn cull(&mut self, mut vertices: [ClipVertex; 3]) -> Option<[ClipVertex; 3]> {
        if self.front_face == FrontFace::CounterClockwise {
            vertices.swap(1, 2);
        }

        match self.cull_mode {
            CullMode::BackFace => {
                //Back face culling is always done in hardware, other modes have to be done in
                //software.
            },
            CullMode::None|CullMode::FrontFace => {
                fn xy(v: ClipVertex) -> Vec2 {
                    let v = Vec4::from_array(v.pos);
                    v.xy() / v.w
                }
                fn orient2d(a: Vec2, b: Vec2, c: Vec2) -> f32 {
                    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
                }

                let orientation = orient2d(xy(vertices[0]), xy(vertices[1]), xy(vertices[2]));
                if self.cull_mode == CullMode::None && orientation > 0.0 {
                    //If culling is disabled but this triangle would be skipped during rasterization,
                    //change the winding order to draw it
                    vertices.swap(1, 2);
                } else if self.cull_mode == CullMode::FrontFace {
                    //If front face culling is enabled and this triangle is front facing, skip it.
                    if orientation < 0.0 {
                        return None;
                    }
                    //Otherwise, change winding order to avoid hardware culling
                    vertices.swap(1, 2);
                }
            }
        }

        return Some(vertices);
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
