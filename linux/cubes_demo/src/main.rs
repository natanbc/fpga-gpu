use glam::{Mat4, Vec3};
use umd::gl::GouraudVertex;
#[cfg(feature = "software")]
use umd::gl::{SoftwareGl as Gl};
#[cfg(not(feature = "software"))]
use umd::gl::{HardwareGl as Gl};

#[tokio::main]
async fn main() {
    let mut gl = Gl::new().unwrap();

    let positions = [
        [-1.0f32, -1.0f32, -1.0f32],
        [-1.0f32, -1.0f32,  1.0f32],
        [-1.0f32,  1.0f32, -1.0f32],
        [-1.0f32,  1.0f32,  1.0f32],
        [ 1.0f32, -1.0f32, -1.0f32],
        [ 1.0f32, -1.0f32,  1.0f32],
        [ 1.0f32,  1.0f32, -1.0f32],
        [ 1.0f32,  1.0f32,  1.0f32],
    ];
    let colors: [[f32; 3]; 6] = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 1.0, 0.0],
        [1.0, 0.0, 1.0],
        [0.0, 1.0, 1.0],
    ];
    let indices = [
        // v0, v1, v2, color
        [0, 2, 3, 0],
        [0, 3, 1, 0],
        [2, 6, 7, 1],
        [2, 7, 3, 1],
        [6, 4, 5, 2],
        [6, 5, 7, 2],
        [4, 0, 1, 3],
        [4, 1, 5, 3],
        [0, 4, 6, 4],
        [0, 6, 2, 4],
        [1, 7, 5, 5],
        [1, 3, 7, 5],
    ];
    let o_x = 0.7;
    let o_y = 0.7;
    let offsets = [
        [-o_x, o_y, 0.0],
        [0.0, o_y, 0.0],
        [o_x, o_y, 0.0],
        [-o_x, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [o_x, 0.0, 0.0],
        [-o_x, -o_y, 0.0],
        [0.0, -o_y, 0.0],
        [o_x, -o_y, 0.0],
    ];

    let mut vbo = Vec::new();
    let mut ib = Vec::new();
    for [v0, v1, v2, c] in indices {
        for v in [v0, v1, v2] {
            ib.push(vbo.len() as u16);
            vbo.push(GouraudVertex {
                x: positions[v][0],
                y: positions[v][1],
                z: positions[v][2],
                r: colors[c][0],
                g: colors[c][1],
                b: colors[c][2],
            })
        }
    }

    let mut model = Mat4::from_scale(Vec3::new(0.3, 0.3, 0.3));
    let projection = Mat4::perspective_rh_gl(60.0f32.to_radians(), (gl.width() as f32)/(gl.height() as f32), 1.0, 100.0);
    let view = Mat4::look_at_rh(
        Vec3::new(0.0, 0.0, -2.0),
        Vec3::new(0.0, 0.0, 0.0),
        Vec3::new(0.0, 1.0, 0.0),
    );
    gl.set_view_matrix(view);
    gl.set_projection_matrix(projection);
    gl.set_front_face(umd::gl::FrontFace::Clockwise);
    gl.set_cull_mode(umd::gl::CullMode::BackFace);


    let mut render_times = rolling_stats::Stats::<f32>::new();
    #[cfg(not(feature = "software"))]
    let mut submit_times = rolling_stats::Stats::<f32>::new();
    #[cfg(not(feature = "software"))]
    let mut stall_raster = rolling_stats::Stats::<f32>::new();
    #[cfg(not(feature = "software"))]
    let mut stall_depth_load_addr = rolling_stats::Stats::<f32>::new();
    #[cfg(not(feature = "software"))]
    let mut stall_depth_load_data = rolling_stats::Stats::<f32>::new();
    #[cfg(not(feature = "software"))]
    let mut stall_depth_store_addr = rolling_stats::Stats::<f32>::new();
    for _ in 0..10_000 {
        let start = std::time::Instant::now();
        let perf = {
            model = Mat4::from_rotation_x(0.006) * Mat4::from_rotation_y(0.012) * Mat4::from_rotation_z(0.018) * model;
            #[cfg(not(feature = "software"))]
            let perf = gl.perf_counters();
            gl.begin_frame().await;

            for offset in offsets {
                gl.set_model_matrix(Mat4::from_translation(Vec3::from_array(offset)) * model);
                gl.draw_gouraud(&vbo[..], &ib[..]).await;
            }
            #[cfg(not(feature = "software"))]
            submit_times.update(start.elapsed().as_secs_f32() * 1000.0);

            gl.end_frame(!cfg!(feature = "headless")).await;
            #[cfg(not(feature = "software"))]
            gl.perf_counters().diff(perf)
        };
        render_times.update(start.elapsed().as_secs_f32() * 1000.0);
        println!("Render: {render_times}");
        #[cfg(not(feature = "software"))]
        {
            let t = perf.busy_cycles as f32;
            stall_raster.update(perf.stalls.walker_searching as f32 / t * 100.0);
            stall_depth_load_addr.update(perf.stalls.depth_load_addr as f32 / t * 100.0);
            stall_depth_load_data.update(perf.stalls.depth_load_data as f32 / t * 100.0);
            stall_depth_store_addr.update(perf.stalls.depth_store_addr as f32 / t * 100.0);
            println!("Submit: {submit_times}");
            println!("Raster stalls: {stall_raster}");
            println!("Depth load addr stalls: {stall_depth_load_addr}");
            println!("Depth load data stalls: {stall_depth_load_data}");
            println!("Depth store addr stalls: {stall_depth_store_addr}");
            println!("{perf:#?}");
        }
        #[cfg(feature = "software")] let _ = perf;
    }
}
