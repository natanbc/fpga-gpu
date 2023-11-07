use std::cell::RefCell;
use std::rc::Rc;
use glam::Mat4;
use crate::gl::command::CommandBuffer;
use crate::hal::{Rasterizer, Uio, Userdma};

mod command;
mod common;
mod texture_buffer;

use crate::gl::texture_buffer::TextureBuffer;

pub use common::{
    GouraudVertex,
    TextureVertex,
    CullMode,
    FrontFace
};
use crate::gl::common::GlCommon;

pub struct Gl {
    common: GlCommon,

    rasterizer: Rc<RefCell<Rasterizer>>,

    next_texture_buffer_id: u32,
    loaded_texture_buffers: [u32; 4],
    next_buffer_replace: u32,

    cmd: CommandBuffer,
}

impl Gl {
    pub fn new() -> std::io::Result<Self> {
        let common = GlCommon::new()?;

        //SAFETY: the device tree must be correct
        let rasterizer = Rc::new(RefCell::new(unsafe {
            Rasterizer::new(Uio::open_named("rasterizer")?)?
        }));

        let alloc = Userdma::open()?;
        let cmd = CommandBuffer::new(rasterizer.clone(), &alloc)?;

        let s = Self {
            common,
            rasterizer,

            next_texture_buffer_id: 1,
            loaded_texture_buffers: [0, 0, 0, 0],
            next_buffer_replace: 0,

            cmd,
        };

        unsafe {
            s.rasterizer.borrow_mut().set_buffers(
                s.common.frame_buffers[s.common.frame_buffer_idx].1,
                s.common.depth_buffers[s.common.depth_buffer_idx].1,
            );
        }

        Ok(s)
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
        let id = self.next_texture_buffer_id;
        self.next_texture_buffer_id += 1;
        TextureBuffer::new(id)
    }

    pub async fn begin_frame(&mut self) {
        self.common.begin_frame();

        unsafe {
            self.rasterizer.borrow_mut().set_buffers(
                self.common.frame_buffers[self.common.frame_buffer_idx].1,
                self.common.depth_buffers[self.common.depth_buffer_idx].1,
            );
        }
    }

    pub async fn end_frame(&mut self) {
        //Finish drawing
        self.cmd.sync().await;
        self.cmd.flush_buffer().await;
        self.rasterizer.borrow_mut().wait_cmd().await;

        self.common.end_frame().await;
    }

    pub async fn draw_gouraud(
        &mut self,
        vertex_buffer: &[GouraudVertex],
        index_buffer: &[u16],
    ) {
        for v in self.common.transform_gouraud(vertex_buffer, index_buffer) {
            self.cmd.draw_triangle(
                None,
                v[0],
                v[1],
                v[2],
            ).await;
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

        for v in self.common.transform_texture(vertex_buffer, index_buffer) {
            self.cmd.draw_triangle(
                Some(buffer),
                v[0],
                v[1],
                v[2],
            ).await;
        }
    }
}
