use std::cell::RefCell;
use std::rc::Rc;
use glam::{Mat4, Vec3, Vec4, Vec4Swizzles};
use crate::gl::command::{CommandBuffer, Vertex};
use crate::hal::{DisplayController, DmaBuf, Rasterizer, Uio, Userdma};

mod command;
mod texture_buffer;

use crate::gl::texture_buffer::TextureBuffer;

pub struct GouraudVertex {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub r: f32,
    pub g: f32,
    pub b: f32,
}

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

pub struct Gl {
    dc: DisplayController,
    rasterizer: Rc<RefCell<Rasterizer>>,

    view: Mat4,
    projection: Mat4,
    projection_view: Mat4, /* precomputed projection * view */

    model: Mat4,

    scale_device: Vec3,

    next_texture_buffer_id: u32,
    loaded_texture_buffers: [u32; 4],
    next_buffer_replace: u32,

    cmd: CommandBuffer,

    frame_buffers: [(DmaBuf, usize); 3],
    depth_buffers: [(DmaBuf, usize); 2],
    frame_buffer_idx: usize,
    depth_buffer_idx: usize,

    cull_mode: CullMode,
    front_face: FrontFace,
}

impl Gl {
    pub fn new() -> std::io::Result<Self> {
        //SAFETY: the device tree must be correct
        let dc = unsafe {
            DisplayController::new(Uio::open_named("display_controller")?)?
        };
        let rasterizer = Rc::new(RefCell::new(unsafe {
            Rasterizer::new(Uio::open_named("rasterizer")?)?
        }));

        let width = dc.width() as usize;
        let height = dc.height() as usize;

        let alloc = Userdma::open()?;
        let cmd = CommandBuffer::new(rasterizer.clone(), &alloc)?;

        let fb_size = width * height * 3;
        let fb_size = (fb_size + 4095) / 4096 * 4096;
        let z_size = width * height * 2;
        let z_size = (z_size + 4095) / 4096 * 4096;


        let s = Self {
            dc,
            rasterizer,

            view: Mat4::IDENTITY,
            projection: Mat4::IDENTITY,
            projection_view: Mat4::IDENTITY,

            model: Mat4::IDENTITY,

            scale_device: Vec3::new((width - 1) as f32, (height - 1) as f32, 65535.0),

            next_texture_buffer_id: 1,
            loaded_texture_buffers: [0, 0, 0, 0],
            next_buffer_replace: 0,

            cmd,

            frame_buffers: [
                alloc.alloc_buf(fb_size)?,
                alloc.alloc_buf(fb_size)?,
                alloc.alloc_buf(fb_size)?,
            ],
            depth_buffers: [
                alloc.alloc_buf(z_size)?,
                alloc.alloc_buf(z_size)?,
            ],
            frame_buffer_idx: 0,
            depth_buffer_idx: 0,

            cull_mode: CullMode::BackFace,
            front_face: FrontFace::CounterClockwise,
        };

        unsafe {
            s.rasterizer.borrow_mut().set_buffers(
                s.frame_buffers[s.frame_buffer_idx].1,
                s.depth_buffers[s.depth_buffer_idx].1,
            );
        }

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

    pub fn create_texture_buffer(&mut self) -> TextureBuffer {
        let id = self.next_texture_buffer_id;
        self.next_texture_buffer_id += 1;
        TextureBuffer::new(id)
    }

    pub async fn begin_frame(&mut self) {
        let fb = &mut self.frame_buffers[self.frame_buffer_idx].0;
        let fb_map = fb.map().unwrap();
        fb.with_sync(|| {
            unsafe {
                libc::memset(*fb_map, 0xFF, fb_map.size());
            }
        });

        let depth = &mut self.depth_buffers[self.depth_buffer_idx].0;
        let depth_map = depth.map().unwrap();
        depth.with_sync(|| {
            unsafe {
                libc::memset(*depth_map, 0, depth_map.size());
            }
        });

        unsafe {
            self.rasterizer.borrow_mut().set_buffers(
                self.frame_buffers[self.frame_buffer_idx].1,
                self.depth_buffers[self.depth_buffer_idx].1,
            );
        }
    }

    pub async fn end_frame(&mut self) {
        //Finish drawing
        self.cmd.sync().await;
        self.cmd.flush_buffer().await;
        self.rasterizer.borrow_mut().wait_cmd().await;

        //TODO: buffer_clearer async clearing
        unsafe {
            self.dc.draw_frame(self.frame_buffers[self.frame_buffer_idx].1);
        }
        self.dc.wait_end_of_frame().await;

        self.frame_buffer_idx = (self.frame_buffer_idx + 1) % self.frame_buffers.len();
        self.depth_buffer_idx = (self.depth_buffer_idx + 1) % self.depth_buffers.len();
    }

    pub async fn draw_gouraud(
        &mut self,
        vertex_buffer: &[GouraudVertex],
        index_buffer: &[u16],
    ) {
        assert_eq!(index_buffer.len() % 3, 0);
        let mut vertices = [
            Vertex {
                x: 0,
                y: 0,
                z: 0,
                r_s: 0,
                g_t: 0,
                b: 0,
            }; 3
        ];
        for (i, idx) in index_buffer.iter().enumerate() {
            let vertex = &vertex_buffer[*idx as usize];
            let transformed = self.transform_vertex(vertex.x, vertex.y, vertex.z);
            vertices[i % 3] = Vertex {
                x: transformed[0],
                y: transformed[1],
                z: transformed[2],
                r_s: (vertex.r * 255.0) as u8,
                g_t: (vertex.g * 255.0) as u8,
                b: (vertex.b * 255.0) as u8,
            };
            if i % 3 == 2 {
                self.draw(None, &mut vertices).await;
            }
        }
    }

    pub async fn draw_texture(
        &mut self,
        texture_buffer: &TextureBuffer,
        vertex_buffer: &[TextureVertex],
        index_buffer: &[u16],
    ) {
        assert_eq!(index_buffer.len() % 3, 0);
        let (buffer, load) = match self.loaded_texture_buffers.iter().position(|v| *v == texture_buffer.id) {
            None => {
                let idx = self.next_buffer_replace;
                self.next_buffer_replace = (idx + 1) % 4;
                (idx as u8, true)
            },
            Some(x) => (x as u8, texture_buffer.dirty.get()),
        };
        if load {
            const QUADRANT_SIZE: usize = 64 * 64 * 3;
            self.cmd.load_texture(buffer, 0, 63, 0, 63, &texture_buffer.data[..QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 0, 63, 64, 127, &texture_buffer.data[QUADRANT_SIZE..2 * QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 64, 127, 0, 63, &texture_buffer.data[2 * QUADRANT_SIZE.. 3 * QUADRANT_SIZE]).await;
            self.cmd.load_texture(buffer, 64, 127, 64, 127, &texture_buffer.data[3 * QUADRANT_SIZE..]).await;
            texture_buffer.dirty.set(false);
        }

        let mut vertices = [
            Vertex {
                x: 0,
                y: 0,
                z: 0,
                r_s: 0,
                g_t: 0,
                b: 0,
            }; 3
        ];
        for (i, idx) in index_buffer.iter().enumerate() {
            let vertex = &vertex_buffer[*idx as usize];
            let transformed = self.transform_vertex(vertex.x, vertex.y, vertex.z);
            vertices[i % 3] = Vertex {
                x: transformed[0],
                y: transformed[1],
                z: transformed[2],
                r_s: ((1.0 - vertex.s) * 255.0) as u8,
                g_t: (vertex.t * 255.0) as u8,
                b: 0
            };
            if i % 3 == 2 {
                self.draw(Some(buffer), &mut vertices).await;
            }
        }
    }

    async fn draw(&mut self, texture: Option<u8>, vertices: &mut [Vertex; 3]) {
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
                fn orient2d(a: Vertex, b: Vertex, c: Vertex) -> i32 {
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
                    return;
                }
            }
        }
        self.cmd.draw_triangle(
            texture,
            vertices[0],
            vertices[1],
            vertices[2],
        ).await;
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
