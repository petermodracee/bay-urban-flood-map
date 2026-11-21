import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_OUTPUTS_DIR = path.resolve(__dirname, '../data/web_outputs');

const MIME_TYPES = {
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json; charset=utf-8',
  '.pmtiles': 'application/octet-stream',
};

/**
 * Serve ../data/web_outputs at /data/ with HTTP Range support so
 * @geomatico/maplibre-cog-protocol can stream local COGs during dev/preview.
 */
function serveWebOutputsPlugin() {
  const middleware = (req, res, next) => {
    if (!req.url || !req.url.startsWith('/data/')) {
      return next();
    }

    const reqPath = decodeURIComponent(req.url.split('?')[0].slice('/data/'.length));
    const filePath = path.resolve(WEB_OUTPUTS_DIR, reqPath);
    if (!filePath.startsWith(WEB_OUTPUTS_DIR + path.sep) || !fs.existsSync(filePath)) {
      return next();
    }

    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      return next();
    }

    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || 'application/octet-stream';
    res.setHeader('Content-Type', contentType);
    res.setHeader('Accept-Ranges', 'bytes');

    if (req.method === 'HEAD') {
      res.setHeader('Content-Length', stat.size);
      res.statusCode = 200;
      res.end();
      return;
    }

    const rangeHeader = req.headers.range;
    if (rangeHeader) {
      const match = /bytes=(\d*)-(\d*)/.exec(rangeHeader);
      if (match) {
        const start = match[1] ? Number.parseInt(match[1], 10) : 0;
        const end = match[2] ? Number.parseInt(match[2], 10) : stat.size - 1;
        if (start >= stat.size || end >= stat.size || start > end) {
          res.statusCode = 416;
          res.setHeader('Content-Range', `bytes */${stat.size}`);
          res.end();
          return;
        }
        res.statusCode = 206;
        res.setHeader('Content-Range', `bytes ${start}-${end}/${stat.size}`);
        res.setHeader('Content-Length', end - start + 1);
        fs.createReadStream(filePath, { start, end }).pipe(res);
        return;
      }
    }

    res.statusCode = 200;
    res.setHeader('Content-Length', stat.size);
    fs.createReadStream(filePath).pipe(res);
  };

  return {
    name: 'serve-data-web-outputs',
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

export default defineConfig({
  plugins: [serveWebOutputsPlugin()],
});
