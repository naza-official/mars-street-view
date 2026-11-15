import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, resolve } from 'node:path';
import { PerspectiveCamera, Vector3 } from 'three';
import { Ellipsoid, TilesRenderer } from '3d-tiles-renderer';
import { QuantizedMeshPlugin } from '3d-tiles-renderer/plugins';

const MARS_R = 3396190;
const [rootDir, entry, mode = '3dtiles', altitudeArg = '300000'] = process.argv.slice(2);
const altitude = Number(altitudeArg);
const TYPES = { '.json': 'application/json', '.glb': 'model/gltf-binary', '.terrain': 'application/vnd.quantized-mesh' };

function serve(dir) {
	return new Promise((done) => {
		const server = createServer(async (req, res) => {
			try {
				const body = await readFile(join(resolve(dir), decodeURIComponent(req.url.split('?')[0])));
				res.writeHead(200, { 'content-type': TYPES[extname(req.url.split('?')[0])] || 'application/octet-stream' });
				res.end(body);
			} catch {
				res.writeHead(404);
				res.end();
			}
		});
		server.listen(0, () => done(server));
	});
}

function lonLatToXyz(lon, lat, h) {
	const r = MARS_R + h, la = lat * Math.PI / 180, lo = lon * Math.PI / 180;
	return new Vector3(r * Math.cos(la) * Math.cos(lo), r * Math.cos(la) * Math.sin(lo), r * Math.sin(la));
}

function makeCamera() {
	const target = lonLatToXyz(77.5, 18.4, -3000);
	const camera = new PerspectiveCamera(60, 1024 / 768, 100, 1e8);
	camera.position.copy(lonLatToXyz(77.5, 18.4, altitude));
	camera.up.set(0, 0, 1);
	camera.lookAt(target);
	camera.updateMatrixWorld();
	return camera;
}

function measure(scene, stats) {
	scene.updateMatrixWorld(true);
	scene.traverse((obj) => {
		const pos = obj.geometry?.attributes?.position;
		if (!pos) return;
		const v = new Vector3();
		for (let i = 0; i < pos.count; i++) {
			v.fromBufferAttribute(pos, i).applyMatrix4(obj.matrixWorld);
			const h = v.length() - MARS_R;
			stats.min = Math.min(stats.min, h);
			stats.max = Math.max(stats.max, h);
			stats.vertices++;
		}
	});
}

const server = await serve(rootDir);
const url = `http://localhost:${server.address().port}/${entry}`;
globalThis.window = { location: { href: url }, devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} };
globalThis.location = globalThis.window.location;
globalThis.self = globalThis;
globalThis.ImageBitmap = class ImageBitmap { constructor() { this.width = 1; this.height = 1; } close() {} };
globalThis.createImageBitmap = async () => new globalThis.ImageBitmap();
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
const tiles = new TilesRenderer(url);
tiles.ellipsoid = new Ellipsoid(MARS_R, MARS_R, MARS_R);
if (mode === 'qmesh') tiles.registerPlugin(new QuantizedMeshPlugin({ skirtLength: 0 }));
const camera = makeCamera();
tiles.setCamera(camera);
tiles.setResolution(camera, 1024, 768);
const stats = { models: 0, errors: [], vertices: 0, min: Infinity, max: -Infinity, maxDepth: 0, byDepth: {} };
tiles.addEventListener('load-model', ({ scene, tile }) => {
	stats.models++;
	stats.maxDepth = Math.max(stats.maxDepth, tile.__depth ?? tile.internal?.depth ?? 0);
	tiles.group.updateMatrixWorld(true);
	const depth = tile.__depth ?? tile.internal?.depth ?? 0;
	const d = stats.byDepth[depth] ??= { vertices: 0, min: Infinity, max: -Infinity };
	measure(scene, d);
	measure(scene, stats);
});
tiles.addEventListener('load-error', ({ error, url: u }) => stats.errors.push(`${u}: ${error?.message ?? error}`));
const started = Date.now();
let settled = 0;
const minMs = Number(process.env.MIN_MS ?? 0);
while (Date.now() - started < 30000 && (settled < 40 || Date.now() - started < minMs)) {
	camera.updateMatrixWorld();
	tiles.update();
	await new Promise((r) => setTimeout(r, 25));
	settled = stats.models > 0 && tiles.loadProgress === 1 ? settled + 1 : 0;
}
let visible = 0;
tiles.group.traverse((obj) => { if (obj.geometry && obj.visible) visible++; });
server.close();
console.log(JSON.stringify({
	entry, mode, altitude, visibleMeshes: visible, ms: Date.now() - started, loadProgress: tiles.loadProgress, models: stats.models,
	maxDepth: stats.maxDepth, vertices: stats.vertices,
	worldHeightAboveSphere_m: [Math.round(stats.min * 100) / 100, Math.round(stats.max * 100) / 100],
	byDepth: Object.fromEntries(Object.entries(stats.byDepth).map(([k, v]) => [k, [Math.round(v.min), Math.round(v.max), v.vertices]])),
	errors: stats.errors.slice(0, 5), errorCount: stats.errors.length,
}, null, 1));
tiles.dispose();
process.exit(0);
