use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use sha1::{Digest, Sha1};
use std::collections::HashMap;
use std::fs::File;
use std::hash::{Hash, Hasher};
use std::io::Write;
use std::path::PathBuf;
use std::time::Instant;

const NATIVE_CAPABILITIES: &[&str] = &[
    "accurate_v1",
    "positions_v1",
    "binary_v1",
    "positions_tick_v1",
    "full_hash_v1",
    "loop_map_v1",
    "uv0_tick_v1",
    "skin_v1",
    "submesh_v1",
    "multi_uv_v1",
];
const MAX_SECONDARY_UV_LAYERS: usize = 7;
const MAX_SECONDARY_UV_VALUES: usize = MAX_SECONDARY_UV_LAYERS * 2;

#[derive(Clone, Copy, Debug)]
struct MeshKey {
    v_idx: i32,
    u: i32,
    v: i32,
    nx: i32,
    ny: i32,
    nz: i32,
    secondary_uv_values: [i32; MAX_SECONDARY_UV_VALUES],
    secondary_uv_value_count: u8,
}

impl PartialEq for MeshKey {
    fn eq(&self, other: &Self) -> bool {
        let value_count = self.secondary_uv_value_count as usize;
        self.v_idx == other.v_idx
            && self.u == other.u
            && self.v == other.v
            && self.nx == other.nx
            && self.ny == other.ny
            && self.nz == other.nz
            && self.secondary_uv_value_count == other.secondary_uv_value_count
            && self.secondary_uv_values[..value_count] == other.secondary_uv_values[..value_count]
    }
}

impl Eq for MeshKey {}

impl Hash for MeshKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.v_idx.hash(state);
        self.u.hash(state);
        self.v.hash(state);
        self.nx.hash(state);
        self.ny.hash(state);
        self.nz.hash(state);
        self.secondary_uv_value_count.hash(state);
        self.secondary_uv_values[..self.secondary_uv_value_count as usize].hash(state);
    }
}

fn quantize(value: f32, scale: f32) -> i32 {
    (value * scale) as i32
}

fn get_usize(raw: &Bound<'_, PyDict>, key: &str) -> PyResult<usize> {
    raw.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing {key}")))?
        .extract::<usize>()
}

fn get_bool(raw: &Bound<'_, PyDict>, key: &str, default: bool) -> PyResult<bool> {
    match raw.get_item(key)? {
        Some(value) => value.extract::<bool>(),
        None => Ok(default),
    }
}

fn get_f32_vec(raw: &Bound<'_, PyDict>, key: &str, required: bool) -> PyResult<Vec<f32>> {
    match raw.get_item(key)? {
        Some(value) if !value.is_none() => {
            let bytes: &Bound<'_, PyBytes> = value.downcast()?;
            let data = bytes.as_bytes();
            if data.len() % 4 != 0 {
                return Err(PyValueError::new_err(format!(
                    "{key} byte length is not divisible by 4"
                )));
            }
            Ok(bytemuck::cast_slice::<u8, f32>(data).to_vec())
        }
        _ if required => Err(PyValueError::new_err(format!("missing {key}"))),
        _ => Ok(Vec::new()),
    }
}

fn get_i32_vec(raw: &Bound<'_, PyDict>, key: &str, required: bool) -> PyResult<Vec<i32>> {
    match raw.get_item(key)? {
        Some(value) if !value.is_none() => {
            let bytes: &Bound<'_, PyBytes> = value.downcast()?;
            let data = bytes.as_bytes();
            if data.len() % 4 != 0 {
                return Err(PyValueError::new_err(format!(
                    "{key} byte length is not divisible by 4"
                )));
            }
            Ok(bytemuck::cast_slice::<u8, i32>(data).to_vec())
        }
        _ if required => Err(PyValueError::new_err(format!("missing {key}"))),
        _ => Ok(Vec::new()),
    }
}

fn sha1_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha1::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn f32_bytes(values: &[f32]) -> &[u8] {
    bytemuck::cast_slice::<f32, u8>(values)
}

fn i32_bytes(values: &[i32]) -> &[u8] {
    bytemuck::cast_slice::<i32, u8>(values)
}

#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

fn get_f32_vec_list(raw: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<Vec<f32>>> {
    let Some(value) = raw.get_item(key)? else {
        return Ok(Vec::new());
    };
    if value.is_none() {
        return Ok(Vec::new());
    }

    let values: &Bound<'_, PyList> = value.downcast()?;
    if values.len() > MAX_SECONDARY_UV_LAYERS {
        return Err(PyValueError::new_err(format!(
            "{key} exceeds {MAX_SECONDARY_UV_LAYERS} channels"
        )));
    }
    let mut result = Vec::with_capacity(values.len());
    for (index, item) in values.iter().enumerate() {
        let bytes: &Bound<'_, PyBytes> = item.downcast()?;
        let data = bytes.as_bytes();
        if data.len() % 4 != 0 {
            return Err(PyValueError::new_err(format!(
                "{key}[{index}] byte length is not divisible by 4"
            )));
        }
        result.push(bytemuck::cast_slice::<u8, f32>(data).to_vec());
    }
    Ok(result)
}

fn mesh_key(
    v_idx: usize,
    loop_idx: usize,
    u: f32,
    v: f32,
    nx: f32,
    ny: f32,
    nz: f32,
    secondary_uv_layers: &[Vec<f32>],
    quantize_scale: f32,
) -> MeshKey {
    let mut uv_key = [0; MAX_SECONDARY_UV_VALUES];
    let channel_base = loop_idx * 2;
    for (channel_index, channel) in secondary_uv_layers.iter().enumerate() {
        let output_base = channel_index * 2;
        if channel_base + 1 < channel.len() {
            uv_key[output_base] = quantize(channel[channel_base], quantize_scale);
            uv_key[output_base + 1] = quantize(channel[channel_base + 1], quantize_scale);
        }
    }

    MeshKey {
        v_idx: v_idx as i32,
        u: quantize(u, quantize_scale),
        v: quantize(v, quantize_scale),
        nx: quantize(nx, quantize_scale),
        ny: quantize(ny, quantize_scale),
        nz: quantize(nz, quantize_scale),
        secondary_uv_values: uv_key,
        secondary_uv_value_count: (secondary_uv_layers.len() * 2) as u8,
    }
}

#[pyfunction]
fn capabilities() -> Vec<&'static str> {
    NATIVE_CAPABILITIES.to_vec()
}

#[pyfunction]
#[pyo3(signature = (positions_bytes, source_indices_bytes, output_path=None, previous_hash=None))]
fn gather_positions_v1(
    py: Python<'_>,
    positions_bytes: &Bound<'_, PyBytes>,
    source_indices_bytes: &Bound<'_, PyBytes>,
    output_path: Option<String>,
    previous_hash: Option<String>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();

    let position_data = positions_bytes.as_bytes();
    if position_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "positions byte length is not divisible by 4",
        ));
    }
    let source_data = source_indices_bytes.as_bytes();
    if source_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "sourceIndices byte length is not divisible by 4",
        ));
    }

    let positions = bytemuck::cast_slice::<u8, f32>(position_data);
    let source_indices = bytemuck::cast_slice::<u8, i32>(source_data);
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let gather_start = Instant::now();
    let mut gathered: Vec<f32> = Vec::with_capacity(source_indices.len() * 3);
    for source_index in source_indices {
        if *source_index < 0 {
            return Err(PyValueError::new_err("negative source index"));
        }
        let base = (*source_index as usize) * 3;
        if base + 2 >= positions.len() {
            return Err(PyValueError::new_err("source index out of range"));
        }
        gathered.extend_from_slice(&positions[base..base + 3]);
    }
    let gather_ms = gather_start.elapsed().as_secs_f64() * 1000.0;

    let output_bytes = f32_bytes(&gathered);

    let hash_start = Instant::now();
    let positions_hash = sha1_hex(output_bytes);
    let hash_ms = hash_start.elapsed().as_secs_f64() * 1000.0;
    let changed = match previous_hash {
        Some(prev) => prev != positions_hash,
        None => true,
    };

    let write_start = Instant::now();
    let mut wrote_path = String::new();
    if changed {
        if let Some(ref path) = output_path {
            let mut file = File::create(path)
                .map_err(|e| PyValueError::new_err(format!("create output failed: {e}")))?;
            file.write_all(output_bytes)
                .map_err(|e| PyValueError::new_err(format!("write output failed: {e}")))?;
            wrote_path = path.clone();
        }
    }
    let write_ms = write_start.elapsed().as_secs_f64() * 1000.0;

    let result = PyDict::new_bound(py);
    result.set_item("ok", true)?;
    result.set_item("mode", "positions_tick_v1")?;
    result.set_item("changed", changed)?;
    result.set_item("positionsHash", positions_hash)?;
    result.set_item("exportVertexCount", source_indices.len())?;
    result.set_item("byteLength", if changed { output_bytes.len() } else { 0 })?;
    if !wrote_path.is_empty() {
        result.set_item("path", wrote_path)?;
    } else if changed && output_path.is_none() {
        result.set_item("positions", PyBytes::new_bound(py, output_bytes))?;
    }

    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("gatherMs", gather_ms)?;
    profile.set_item("hashMs", hash_ms)?;
    profile.set_item("writeMs", write_ms)?;
    profile.set_item("totalMs", total_start.elapsed().as_secs_f64() * 1000.0)?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pyfunction]
#[pyo3(signature = (raw, output_dir=None, prefix=None))]
fn extract_mesh_binary_v1(
    py: Python<'_>,
    raw: &Bound<'_, PyDict>,
    output_dir: Option<String>,
    prefix: Option<String>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();

    let vertex_count = get_usize(raw, "vertex_count")?;
    let tri_count = get_usize(raw, "tri_count")?;
    let have_loop_normals = get_bool(raw, "have_loop_normals", true)?;
    let quantize_scale = match raw.get_item("quantize_scale")? {
        Some(value) => value.extract::<f32>()?,
        None => 1_000_000.0,
    };

    let positions = get_f32_vec(raw, "positions", true)?;
    let vertex_normals = get_f32_vec(raw, "vertex_normals", true)?;
    let loop_vertex_indices = get_i32_vec(raw, "loop_vertex_indices", true)?;
    let loop_normals = get_f32_vec(raw, "loop_normals", have_loop_normals)?;
    let tri_loops = get_i32_vec(raw, "tri_loops", true)?;
    let uv0_input = get_f32_vec(raw, "uv0", false)?;
    let secondary_uv_layers = get_f32_vec_list(raw, "secondary_uv_layers")?;

    if positions.len() < vertex_count * 3 {
        return Err(PyValueError::new_err(
            "positions shorter than vertex_count * 3",
        ));
    }
    if vertex_normals.len() < vertex_count * 3 {
        return Err(PyValueError::new_err(
            "vertex_normals shorter than vertex_count * 3",
        ));
    }
    if tri_loops.len() < tri_count * 3 {
        return Err(PyValueError::new_err(
            "tri_loops shorter than tri_count * 3",
        ));
    }
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let dedupe_start = Instant::now();
    let mut vertices: Vec<f32> = Vec::with_capacity(vertex_count * 3);
    let mut normals: Vec<f32> = Vec::with_capacity(vertex_count * 3);
    let mut uv0: Vec<f32> = Vec::with_capacity(vertex_count * 2);
    let mut indices: Vec<i32> = Vec::with_capacity(tri_count * 3);
    let mut source_indices: Vec<i32> = Vec::with_capacity(vertex_count);
    let mut source_loop_indices: Vec<i32> = Vec::with_capacity(vertex_count);
    let mut map: HashMap<MeshKey, i32> = HashMap::with_capacity(vertex_count * 2);
    let mut next_index: i32 = 0;

    for tri_i in 0..tri_count {
        let base = tri_i * 3;
        for k in 0..3 {
            let loop_idx = tri_loops[base + k] as usize;
            if loop_idx >= loop_vertex_indices.len() {
                return Err(PyValueError::new_err("loop index out of range"));
            }
            let v_idx = loop_vertex_indices[loop_idx] as usize;
            if v_idx >= vertex_count {
                return Err(PyValueError::new_err("vertex index out of range"));
            }

            let (u, vv) = if !uv0_input.is_empty() && loop_idx * 2 + 1 < uv0_input.len() {
                (uv0_input[loop_idx * 2], uv0_input[loop_idx * 2 + 1])
            } else {
                (0.0, 0.0)
            };

            let (nx, ny, nz) = if have_loop_normals && loop_idx * 3 + 2 < loop_normals.len() {
                (
                    loop_normals[loop_idx * 3],
                    loop_normals[loop_idx * 3 + 1],
                    loop_normals[loop_idx * 3 + 2],
                )
            } else {
                (
                    vertex_normals[v_idx * 3],
                    vertex_normals[v_idx * 3 + 1],
                    vertex_normals[v_idx * 3 + 2],
                )
            };

            let key = mesh_key(
                v_idx,
                loop_idx,
                u,
                vv,
                nx,
                ny,
                nz,
                &secondary_uv_layers,
                quantize_scale,
            );

            let mapped = if let Some(mapped) = map.get(&key) {
                *mapped
            } else {
                let c_base = v_idx * 3;
                vertices.extend_from_slice(&positions[c_base..c_base + 3]);
                normals.extend_from_slice(&[nx, ny, nz]);
                uv0.extend_from_slice(&[u, vv]);
                source_indices.push(v_idx as i32);
                source_loop_indices.push(loop_idx as i32);
                let mapped = next_index;
                map.insert(key, mapped);
                next_index += 1;
                mapped
            };
            indices.push(mapped);
        }
    }
    let dedupe_ms = dedupe_start.elapsed().as_secs_f64() * 1000.0;

    let write_start = Instant::now();
    let dir = PathBuf::from(output_dir.ok_or_else(|| PyValueError::new_err("missing output_dir"))?);
    std::fs::create_dir_all(&dir)
        .map_err(|e| PyValueError::new_err(format!("create output dir failed: {e}")))?;
    let prefix = prefix.unwrap_or_else(|| "mesh".to_string());
    let pos_path = dir.join(format!("{prefix}_positions.bin"));
    let idx_path = dir.join(format!("{prefix}_indices.bin"));
    let nrm_path = dir.join(format!("{prefix}_normals.bin"));
    let uv_path = dir.join(format!("{prefix}_uv0.bin"));

    File::create(&pos_path)
        .and_then(|mut f| f.write_all(f32_bytes(&vertices)))
        .map_err(|e| PyValueError::new_err(format!("write positions failed: {e}")))?;
    File::create(&idx_path)
        .and_then(|mut f| f.write_all(i32_bytes(&indices)))
        .map_err(|e| PyValueError::new_err(format!("write indices failed: {e}")))?;
    File::create(&nrm_path)
        .and_then(|mut f| f.write_all(f32_bytes(&normals)))
        .map_err(|e| PyValueError::new_err(format!("write normals failed: {e}")))?;
    File::create(&uv_path)
        .and_then(|mut f| f.write_all(f32_bytes(&uv0)))
        .map_err(|e| PyValueError::new_err(format!("write uv0 failed: {e}")))?;
    let write_ms = write_start.elapsed().as_secs_f64() * 1000.0;
    let pos_path = pos_path.to_string_lossy().into_owned();
    let idx_path = idx_path.to_string_lossy().into_owned();
    let nrm_path = nrm_path.to_string_lossy().into_owned();
    let uv_path = uv_path.to_string_lossy().into_owned();

    let hash_start = Instant::now();
    let vertex_sha = sha1_hex(f32_bytes(&vertices));
    let normal_sha = sha1_hex(f32_bytes(&normals));
    let uv_sha = sha1_hex(f32_bytes(&uv0));
    let index_sha = sha1_hex(i32_bytes(&indices));
    let source_sha = sha1_hex(i32_bytes(&source_indices));
    let source_loop_sha = sha1_hex(i32_bytes(&source_loop_indices));
    let mut topology_hasher = Sha1::new();
    topology_hasher.update(i32_bytes(&loop_vertex_indices));
    topology_hasher.update(i32_bytes(&tri_loops));
    let topology_sha = format!("{:x}", topology_hasher.finalize());
    let hash_ms = hash_start.elapsed().as_secs_f64() * 1000.0;

    let export_vertex_count = vertices.len() / 3;
    let index_count = indices.len();
    let result = PyDict::new_bound(py);
    result.set_item("ok", true)?;
    result.set_item("mode", "binary_v1")?;
    result.set_item("vertexCount", export_vertex_count)?;
    result.set_item("indexCount", index_count)?;
    result.set_item(
        "sourceIndices",
        PyBytes::new_bound(py, i32_bytes(&source_indices)),
    )?;
    result.set_item(
        "sourceLoopIndices",
        PyBytes::new_bound(py, i32_bytes(&source_loop_indices)),
    )?;

    let buffers = pyo3::types::PyList::empty_bound(py);
    let add_buffer = |semantic: &str,
                      format: &str,
                      components: usize,
                      count: usize,
                      path: &str,
                      byte_length: usize|
     -> PyResult<()> {
        let b = PyDict::new_bound(py);
        b.set_item("semantic", semantic)?;
        b.set_item("format", format)?;
        b.set_item("components", components)?;
        b.set_item("count", count)?;
        b.set_item("path", path)?;
        b.set_item("byteLength", byte_length)?;
        buffers.append(b)?;
        Ok(())
    };
    add_buffer(
        "POSITION",
        "float32",
        3,
        export_vertex_count,
        &pos_path,
        f32_bytes(&vertices).len(),
    )?;
    add_buffer(
        "INDEX",
        "int32",
        1,
        index_count,
        &idx_path,
        i32_bytes(&indices).len(),
    )?;
    add_buffer(
        "NORMAL",
        "float32",
        3,
        export_vertex_count,
        &nrm_path,
        f32_bytes(&normals).len(),
    )?;
    add_buffer(
        "UV0",
        "float32",
        2,
        export_vertex_count,
        &uv_path,
        f32_bytes(&uv0).len(),
    )?;
    result.set_item("buffers", buffers)?;

    let total_binary_bytes = f32_bytes(&vertices).len()
        + i32_bytes(&indices).len()
        + f32_bytes(&normals).len()
        + f32_bytes(&uv0).len();
    let hash_profile = PyDict::new_bound(py);
    hash_profile.set_item("vertexSha1", vertex_sha.clone())?;
    hash_profile.set_item("normalSha1", normal_sha.clone())?;
    hash_profile.set_item("uv0Sha1", uv_sha.clone())?;
    hash_profile.set_item("indexSha1", index_sha.clone())?;
    hash_profile.set_item("sourceIndexSha1", source_sha.clone())?;
    hash_profile.set_item("sourceLoopIndexSha1", source_loop_sha.clone())?;
    hash_profile.set_item("topologySha1", topology_sha.clone())?;
    hash_profile.set_item("exportVertexCount", export_vertex_count)?;
    hash_profile.set_item("indexCount", index_count)?;
    hash_profile.set_item("hashMs", hash_ms)?;
    result.set_item("hashProfile", hash_profile)?;

    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("dedupeMs", dedupe_ms)?;
    profile.set_item("writeMs", write_ms)?;
    profile.set_item("hashMs", hash_ms)?;
    profile.set_item("totalMs", total_start.elapsed().as_secs_f64() * 1000.0)?;
    profile.set_item("binaryBytes", total_binary_bytes)?;
    profile.set_item("exportVertexCount", export_vertex_count)?;
    profile.set_item("indexCount", index_count)?;
    profile.set_item("uniqueKeyCount", map.len())?;
    profile.set_item("vertexSha1", vertex_sha)?;
    profile.set_item("normalSha1", normal_sha)?;
    profile.set_item("uv0Sha1", uv_sha)?;
    profile.set_item("indexSha1", index_sha)?;
    profile.set_item("sourceIndexSha1", source_sha)?;
    profile.set_item("sourceLoopIndexSha1", source_loop_sha)?;
    profile.set_item("topologySha1", topology_sha)?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pyfunction]
#[pyo3(signature = (raw, _output_path=None))]
fn extract_mesh_accurate_v1(
    py: Python<'_>,
    raw: &Bound<'_, PyDict>,
    _output_path: Option<String>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();

    let vertex_count = get_usize(raw, "vertex_count")?;
    let tri_count = get_usize(raw, "tri_count")?;
    let have_loop_normals = get_bool(raw, "have_loop_normals", true)?;
    let quantize_scale = match raw.get_item("quantize_scale")? {
        Some(value) => value.extract::<f32>()?,
        None => 1_000_000.0,
    };

    let positions = get_f32_vec(raw, "positions", true)?;
    let vertex_normals = get_f32_vec(raw, "vertex_normals", true)?;
    let loop_vertex_indices = get_i32_vec(raw, "loop_vertex_indices", true)?;
    let loop_normals = get_f32_vec(raw, "loop_normals", have_loop_normals)?;
    let tri_loops = get_i32_vec(raw, "tri_loops", true)?;
    let uv0_input = get_f32_vec(raw, "uv0", false)?;
    let secondary_uv_layers = get_f32_vec_list(raw, "secondary_uv_layers")?;

    if positions.len() < vertex_count * 3 {
        return Err(PyValueError::new_err(
            "positions shorter than vertex_count * 3",
        ));
    }
    if vertex_normals.len() < vertex_count * 3 {
        return Err(PyValueError::new_err(
            "vertex_normals shorter than vertex_count * 3",
        ));
    }
    if tri_loops.len() < tri_count * 3 {
        return Err(PyValueError::new_err(
            "tri_loops shorter than tri_count * 3",
        ));
    }
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let dedupe_start = Instant::now();
    let mut vertices: Vec<f32> = Vec::with_capacity(vertex_count * 3);
    let mut normals: Vec<f32> = Vec::with_capacity(vertex_count * 3);
    let mut uv0: Vec<f32> = Vec::with_capacity(vertex_count * 2);
    let mut indices: Vec<i32> = Vec::with_capacity(tri_count * 3);
    let mut source_indices: Vec<i32> = Vec::with_capacity(vertex_count);
    let mut source_loop_indices: Vec<i32> = Vec::with_capacity(vertex_count);
    let mut map: HashMap<MeshKey, i32> = HashMap::with_capacity(vertex_count * 2);
    let mut next_index: i32 = 0;

    for tri_i in 0..tri_count {
        let base = tri_i * 3;
        for k in 0..3 {
            let loop_idx = tri_loops[base + k] as usize;
            if loop_idx >= loop_vertex_indices.len() {
                return Err(PyValueError::new_err("loop index out of range"));
            }
            let v_idx = loop_vertex_indices[loop_idx] as usize;
            if v_idx >= vertex_count {
                return Err(PyValueError::new_err("vertex index out of range"));
            }

            let (u, vv) = if !uv0_input.is_empty() && loop_idx * 2 + 1 < uv0_input.len() {
                (uv0_input[loop_idx * 2], uv0_input[loop_idx * 2 + 1])
            } else {
                (0.0, 0.0)
            };

            let (nx, ny, nz) = if have_loop_normals && loop_idx * 3 + 2 < loop_normals.len() {
                (
                    loop_normals[loop_idx * 3],
                    loop_normals[loop_idx * 3 + 1],
                    loop_normals[loop_idx * 3 + 2],
                )
            } else {
                (
                    vertex_normals[v_idx * 3],
                    vertex_normals[v_idx * 3 + 1],
                    vertex_normals[v_idx * 3 + 2],
                )
            };

            let key = mesh_key(
                v_idx,
                loop_idx,
                u,
                vv,
                nx,
                ny,
                nz,
                &secondary_uv_layers,
                quantize_scale,
            );

            let mapped = if let Some(mapped) = map.get(&key) {
                *mapped
            } else {
                let c_base = v_idx * 3;
                vertices.extend_from_slice(&positions[c_base..c_base + 3]);
                normals.extend_from_slice(&[nx, ny, nz]);
                uv0.extend_from_slice(&[u, vv]);
                source_indices.push(v_idx as i32);
                source_loop_indices.push(loop_idx as i32);
                let mapped = next_index;
                map.insert(key, mapped);
                next_index += 1;
                mapped
            };
            indices.push(mapped);
        }
    }
    let dedupe_ms = dedupe_start.elapsed().as_secs_f64() * 1000.0;

    let hash_start = Instant::now();
    let vertex_sha = sha1_hex(f32_bytes(&vertices));
    let normal_sha = sha1_hex(f32_bytes(&normals));
    let uv_sha = sha1_hex(f32_bytes(&uv0));
    let index_sha = sha1_hex(i32_bytes(&indices));
    let source_sha = sha1_hex(i32_bytes(&source_indices));
    let hash_ms = hash_start.elapsed().as_secs_f64() * 1000.0;

    let result = PyDict::new_bound(py);
    result.set_item("ok", true)?;
    result.set_item("mode", "pyd_accurate_v1")?;
    result.set_item("vertices", PyBytes::new_bound(py, f32_bytes(&vertices)))?;
    result.set_item("normals", PyBytes::new_bound(py, f32_bytes(&normals)))?;
    result.set_item("uv0", PyBytes::new_bound(py, f32_bytes(&uv0)))?;
    result.set_item("indices", PyBytes::new_bound(py, i32_bytes(&indices)))?;
    result.set_item(
        "sourceIndices",
        PyBytes::new_bound(py, i32_bytes(&source_indices)),
    )?;
    result.set_item(
        "sourceLoopIndices",
        PyBytes::new_bound(py, i32_bytes(&source_loop_indices)),
    )?;

    let hash_profile = PyDict::new_bound(py);
    hash_profile.set_item("vertexSha1", vertex_sha)?;
    hash_profile.set_item("normalSha1", normal_sha)?;
    hash_profile.set_item("uv0Sha1", uv_sha)?;
    hash_profile.set_item("indexSha1", index_sha)?;
    hash_profile.set_item("sourceIndexSha1", source_sha)?;
    hash_profile.set_item("exportVertexCount", vertices.len() / 3)?;
    hash_profile.set_item("indexCount", indices.len())?;
    hash_profile.set_item("hashMs", hash_ms)?;
    result.set_item("hashProfile", hash_profile)?;

    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("dedupeMs", dedupe_ms)?;
    profile.set_item("hashMs", hash_ms)?;
    profile.set_item("totalMs", total_start.elapsed().as_secs_f64() * 1000.0)?;
    profile.set_item("exportVertexCount", vertices.len() / 3)?;
    profile.set_item("indexCount", indices.len())?;
    profile.set_item("uniqueKeyCount", map.len())?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pyfunction]
#[pyo3(signature = (uv_bytes, source_loop_indices_bytes, output_path=None, previous_hash=None))]
fn gather_uv0_v1(
    py: Python<'_>,
    uv_bytes: &Bound<'_, PyBytes>,
    source_loop_indices_bytes: &Bound<'_, PyBytes>,
    output_path: Option<String>,
    previous_hash: Option<String>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();
    let uv_data = uv_bytes.as_bytes();
    if uv_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "uv byte length is not divisible by 4",
        ));
    }
    let source_data = source_loop_indices_bytes.as_bytes();
    if source_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "sourceLoopIndices byte length is not divisible by 4",
        ));
    }
    let uv = bytemuck::cast_slice::<u8, f32>(uv_data);
    let source_loop_indices = bytemuck::cast_slice::<u8, i32>(source_data);
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let gather_start = Instant::now();
    let mut gathered: Vec<f32> = Vec::with_capacity(source_loop_indices.len() * 2);
    for source_loop_index in source_loop_indices {
        if *source_loop_index < 0 {
            return Err(PyValueError::new_err("negative source loop index"));
        }
        let base = (*source_loop_index as usize) * 2;
        if base + 1 >= uv.len() {
            return Err(PyValueError::new_err("source loop index out of range"));
        }
        gathered.extend_from_slice(&uv[base..base + 2]);
    }
    let gather_ms = gather_start.elapsed().as_secs_f64() * 1000.0;

    let output_bytes = f32_bytes(&gathered);
    let hash_start = Instant::now();
    let uv_hash = sha1_hex(output_bytes);
    let hash_ms = hash_start.elapsed().as_secs_f64() * 1000.0;
    let changed = match previous_hash {
        Some(prev) => prev != uv_hash,
        None => true,
    };

    let write_start = Instant::now();
    let mut wrote_path = String::new();
    if changed {
        if let Some(ref path) = output_path {
            let mut file = File::create(path)
                .map_err(|e| PyValueError::new_err(format!("create output failed: {e}")))?;
            file.write_all(output_bytes)
                .map_err(|e| PyValueError::new_err(format!("write output failed: {e}")))?;
            wrote_path = path.clone();
        }
    }
    let write_ms = write_start.elapsed().as_secs_f64() * 1000.0;

    let result = PyDict::new_bound(py);
    result.set_item("ok", true)?;
    result.set_item("mode", "uv0_tick_v1")?;
    result.set_item("changed", changed)?;
    result.set_item("uv0Hash", uv_hash)?;
    result.set_item("exportVertexCount", source_loop_indices.len())?;
    result.set_item("byteLength", if changed { output_bytes.len() } else { 0 })?;
    if !wrote_path.is_empty() {
        result.set_item("path", wrote_path)?;
    } else if changed && output_path.is_none() {
        result.set_item("uv0", PyBytes::new_bound(py, output_bytes))?;
    }
    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("gatherMs", gather_ms)?;
    profile.set_item("hashMs", hash_ms)?;
    profile.set_item("writeMs", write_ms)?;
    profile.set_item("totalMs", total_start.elapsed().as_secs_f64() * 1000.0)?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pyfunction]
fn build_material_submeshes_v1(
    py: Python<'_>,
    indices_bytes: &Bound<'_, PyBytes>,
    material_indices_bytes: &Bound<'_, PyBytes>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();

    let index_data = indices_bytes.as_bytes();
    if index_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "indices byte length is not divisible by 4",
        ));
    }
    let material_data = material_indices_bytes.as_bytes();
    if material_data.len() % 4 != 0 {
        return Err(PyValueError::new_err(
            "materialIndices byte length is not divisible by 4",
        ));
    }
    let indices = bytemuck::cast_slice::<u8, i32>(index_data);
    let material_indices = bytemuck::cast_slice::<u8, i32>(material_data);
    let tri_count = material_indices.len();
    if indices.len() < tri_count * 3 {
        return Err(PyValueError::new_err(
            "indices shorter than material triangle count",
        ));
    }
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let group_start = Instant::now();
    let max_slot = material_indices
        .iter()
        .map(|slot| if *slot < 0 { 0 } else { *slot as usize })
        .max()
        .unwrap_or(0);
    let mut counts = vec![0usize; max_slot + 1];
    for slot in material_indices {
        let normalized = if *slot < 0 { 0 } else { *slot as usize };
        counts[normalized] += 3;
    }
    let mut grouped: Vec<Vec<i32>> = counts
        .iter()
        .map(|count| Vec::with_capacity(*count))
        .collect();
    for (tri_index, slot) in material_indices.iter().enumerate() {
        let normalized = if *slot < 0 { 0 } else { *slot as usize };
        let base = tri_index * 3;
        grouped[normalized].extend_from_slice(&indices[base..base + 3]);
    }
    let group_ms = group_start.elapsed().as_secs_f64() * 1000.0;

    let hash_start = Instant::now();
    let non_empty_count = grouped
        .iter()
        .filter(|slot_indices| !slot_indices.is_empty())
        .count();
    let mut fingerprint_hasher = Sha1::new();
    fingerprint_hasher.update(non_empty_count.to_string().as_bytes());
    let submeshes = PyList::empty_bound(py);
    for (material_slot, slot_indices) in grouped.iter().enumerate() {
        if slot_indices.is_empty() {
            continue;
        }
        fingerprint_hasher.update(b"|slot=");
        fingerprint_hasher.update(material_slot.to_string().as_bytes());
        fingerprint_hasher.update(b"|topology=triangles");
        fingerprint_hasher.update(b"indices|");
        fingerprint_hasher.update(slot_indices.len().to_string().as_bytes());
        fingerprint_hasher.update(b"|i:");
        let slot_index_bytes = i32_bytes(slot_indices);
        fingerprint_hasher.update(slot_index_bytes);
        let index_sha1 = sha1_hex(slot_index_bytes);

        let submesh = PyDict::new_bound(py);
        submesh.set_item("materialSlot", material_slot)?;
        submesh.set_item("topology", "triangles")?;
        submesh.set_item("indices", PyBytes::new_bound(py, slot_index_bytes))?;
        submesh.set_item("indexSha1", index_sha1)?;
        submeshes.append(submesh)?;
    }
    let fingerprint = format!("{:x}", fingerprint_hasher.finalize());
    let hash_ms = hash_start.elapsed().as_secs_f64() * 1000.0;

    let result = PyDict::new_bound(py);
    result.set_item("ok", true)?;
    result.set_item("subMeshes", submeshes)?;
    result.set_item("fingerprint", fingerprint)?;
    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("groupMs", group_ms)?;
    profile.set_item("hashMs", hash_ms)?;
    profile.set_item("totalMs", total_start.elapsed().as_secs_f64() * 1000.0)?;
    profile.set_item("subMeshCount", non_empty_count)?;
    profile.set_item("triCount", tri_count)?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pyfunction]
fn extract_skin_variable_influences_v1(
    py: Python<'_>,
    raw: &Bound<'_, PyDict>,
) -> PyResult<PyObject> {
    let total_start = Instant::now();
    let parse_start = Instant::now();

    let needed_source_vertex_indices = get_i32_vec(raw, "neededSourceVertexIndices", true)?;
    let source_vertex_offsets = get_i32_vec(raw, "sourceVertexOffsets", true)?;
    let source_group_indices = get_i32_vec(raw, "sourceGroupIndices", true)?;
    let source_group_weights = get_f32_vec(raw, "sourceGroupWeights", true)?;
    let group_to_bone_keys = get_i32_vec(raw, "groupToBoneKeys", true)?;
    let group_to_bone_values = get_i32_vec(raw, "groupToBoneValues", true)?;
    let exported_vertex_source_indices = get_i32_vec(raw, "exportedVertexSourceIndices", true)?;
    let fallback_bone_index = match raw.get_item("fallbackBoneIndex")? {
        Some(value) => value.extract::<i32>()?,
        None => -1,
    };

    if source_vertex_offsets.len() != needed_source_vertex_indices.len() + 1 {
        return Err(PyValueError::new_err(
            "sourceVertexOffsets length must be neededSourceVertexIndices length + 1",
        ));
    }
    if source_group_indices.len() != source_group_weights.len() {
        return Err(PyValueError::new_err(
            "sourceGroupIndices/sourceGroupWeights length mismatch",
        ));
    }
    if group_to_bone_keys.len() != group_to_bone_values.len() {
        return Err(PyValueError::new_err(
            "groupToBoneKeys/groupToBoneValues length mismatch",
        ));
    }
    let parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let map_start = Instant::now();
    let mut group_to_bone = HashMap::<i32, i32>::with_capacity(group_to_bone_keys.len());
    for i in 0..group_to_bone_keys.len() {
        group_to_bone.insert(group_to_bone_keys[i], group_to_bone_values[i]);
    }
    let mut row_by_source =
        HashMap::<i32, usize>::with_capacity(needed_source_vertex_indices.len());
    for (row, source_idx) in needed_source_vertex_indices.iter().enumerate() {
        row_by_source.insert(*source_idx, row);
    }
    let map_ms = map_start.elapsed().as_secs_f64() * 1000.0;

    let normalize_start = Instant::now();
    let default_influences: Vec<(i32, f32)> = if fallback_bone_index >= 0 {
        vec![(fallback_bone_index, 1.0)]
    } else {
        Vec::new()
    };
    let mut influences_by_row: Vec<Vec<(i32, f32)>> =
        Vec::with_capacity(needed_source_vertex_indices.len());
    for row in 0..needed_source_vertex_indices.len() {
        let start = source_vertex_offsets[row] as usize;
        let end = source_vertex_offsets[row + 1] as usize;
        if start > end || end > source_group_indices.len() {
            return Err(PyValueError::new_err(
                "sourceVertexOffsets range out of bounds",
            ));
        }
        let mut raw_influences: Vec<(i32, f32)> = Vec::new();
        let mut weight_sum: f32 = 0.0;
        for i in start..end {
            if let Some(bone_index) = group_to_bone.get(&source_group_indices[i]) {
                let weight = source_group_weights[i];
                if weight > 0.0 {
                    raw_influences.push((*bone_index, weight));
                    weight_sum += weight;
                }
            }
        }
        if raw_influences.is_empty() {
            influences_by_row.push(default_influences.clone());
            continue;
        }
        raw_influences.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let inv = if weight_sum != 0.0 {
            1.0 / weight_sum
        } else {
            1.0
        };
        for item in raw_influences.iter_mut() {
            item.1 *= inv;
        }
        influences_by_row.push(raw_influences);
    }
    let normalize_ms = normalize_start.elapsed().as_secs_f64() * 1000.0;

    let emit_start = Instant::now();
    let mut bones_per_vertex: Vec<i32> = Vec::with_capacity(exported_vertex_source_indices.len());
    let mut bone_indices: Vec<i32> = Vec::new();
    let mut bone_weights: Vec<f32> = Vec::new();
    for source_idx in exported_vertex_source_indices.iter() {
        let influences = match row_by_source.get(source_idx) {
            Some(row) => &influences_by_row[*row],
            None => &default_influences,
        };
        bones_per_vertex.push(influences.len() as i32);
        for (bone_index, weight) in influences.iter() {
            bone_indices.push(*bone_index);
            bone_weights.push(*weight);
        }
    }
    let emit_ms = emit_start.elapsed().as_secs_f64() * 1000.0;
    let total_ms = total_start.elapsed().as_secs_f64() * 1000.0;

    let result = PyDict::new_bound(py);
    result.set_item("bonesPerVertex", bones_per_vertex)?;
    result.set_item("boneIndices", bone_indices)?;
    result.set_item("boneWeights", bone_weights)?;
    let profile = PyDict::new_bound(py);
    profile.set_item("parseMs", parse_ms)?;
    profile.set_item("mapMs", map_ms)?;
    profile.set_item("normalizeMs", normalize_ms)?;
    profile.set_item("emitMs", emit_ms)?;
    profile.set_item("totalMs", total_ms)?;
    profile.set_item("neededCount", needed_source_vertex_indices.len())?;
    profile.set_item("exportedCount", exported_vertex_source_indices.len())?;
    profile.set_item("groupCount", source_group_indices.len())?;
    result.set_item("profile", profile)?;
    Ok(result.into())
}

#[pymodule]
fn blendersync_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(capabilities, m)?)?;
    m.add_function(wrap_pyfunction!(extract_mesh_accurate_v1, m)?)?;
    m.add_function(wrap_pyfunction!(extract_skin_variable_influences_v1, m)?)?;
    m.add_function(wrap_pyfunction!(extract_mesh_binary_v1, m)?)?;
    m.add_function(wrap_pyfunction!(gather_positions_v1, m)?)?;
    m.add_function(wrap_pyfunction!(gather_uv0_v1, m)?)?;
    m.add_function(wrap_pyfunction!(build_material_submeshes_v1, m)?)?;
    Ok(())
}
