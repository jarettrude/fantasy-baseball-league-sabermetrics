import fs from 'node:fs';
import path from 'node:path';
import sharp from 'sharp';

const ROOT_DIR = process.cwd();
const PUBLIC_DIR = path.join(ROOT_DIR, 'public');

// Colors
const CRIMSON_LIGHT = '#5e141f';
const CRIMSON_DARK = '#c2384d';
const LEATHER = '#faf5f0';
const INK = '#1b1517';

// Read source SVG
const svg = fs.readFileSync(path.join(ROOT_DIR, 'MooseLogo.svg'), 'utf8');

const crimsonPaths = [];
const whitePaths = [];

const cRegex = /<path d="([^"]+)" style="fill:#5e141f;"\/>/g;
let m;
while ((m = cRegex.exec(svg)) !== null) {
  crimsonPaths.push(m[1]);
}

const wRegex = /<path d="([^"]+)" style="fill:#fff;"\/>/g;
while ((m = wRegex.exec(svg)) !== null) {
  whitePaths.push(m[1]);
}

// 1. Generate logo-themed.svg
const logoThemedSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4267 4267" width="100%" height="100%">
  <defs>
    <style>
      :root {
        --logo-crimson: ${CRIMSON_LIGHT};
        --logo-details: ${INK};
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --logo-crimson: ${CRIMSON_DARK};
          --logo-details: ${LEATHER};
        }
      }
      :root[data-theme="light"] {
        --logo-crimson: ${CRIMSON_LIGHT};
        --logo-details: ${INK};
      }
      :root[data-theme="dark"] {
        --logo-crimson: ${CRIMSON_DARK};
        --logo-details: ${LEATHER};
      }
      .logo-crimson { fill: var(--logo-crimson, ${CRIMSON_LIGHT}); }
      .logo-details { fill: var(--logo-details, ${INK}); }
    </style>
  </defs>
  <g id="logo">
    <g class="logo-crimson">
      ${crimsonPaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
    <g class="logo-details">
      ${whitePaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
  </g>
</svg>`;

fs.writeFileSync(path.join(PUBLIC_DIR, 'logo-themed.svg'), logoThemedSvg, 'utf8');
console.log('Created public/logo-themed.svg');

// 2. Generate favicon.svg with prefers-color-scheme
const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4267 4267">
  <defs>
    <style>
      :root {
        --badge-bg: ${LEATHER};
        --logo-crimson: ${CRIMSON_LIGHT};
        --logo-details: ${INK};
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --badge-bg: ${INK};
          --logo-crimson: ${CRIMSON_DARK};
          --logo-details: ${LEATHER};
        }
      }
      .badge-bg { fill: var(--badge-bg); }
      .logo-crimson { fill: var(--logo-crimson); }
      .logo-details { fill: var(--logo-details); }
    </style>
  </defs>
  <rect class="badge-bg" width="4267" height="4267" rx="850"/>
  <g transform="translate(213, 213) scale(0.9)">
    <g class="logo-crimson">
      ${crimsonPaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
    <g class="logo-details">
      ${whitePaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
  </g>
</svg>`;

fs.writeFileSync(path.join(PUBLIC_DIR, 'favicon.svg'), faviconSvg, 'utf8');
console.log('Created public/favicon.svg');

// 3. Generate high-contrast raster badges (apple-touch-icon, PWA icons)
const badgeSvgForRaster = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4267 4267" width="1024" height="1024">
  <rect fill="${LEATHER}" width="4267" height="4267" rx="850"/>
  <g transform="translate(213, 213) scale(0.9)">
    <g fill="${CRIMSON_LIGHT}">
      ${crimsonPaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
    <g fill="${INK}">
      ${whitePaths.map(d => `<path d="${d}"/>`).join('\n      ')}
    </g>
  </g>
</svg>`;

const svgBuffer = Buffer.from(badgeSvgForRaster);

// Apple touch icon (180x180)
await sharp(svgBuffer)
  .resize(180, 180)
  .png()
  .toFile(path.join(PUBLIC_DIR, 'apple-touch-icon.png'));
console.log('Created public/apple-touch-icon.png');

// PWA 192x192
await sharp(svgBuffer)
  .resize(192, 192)
  .png()
  .toFile(path.join(PUBLIC_DIR, 'favicon-192.png'));
console.log('Created public/favicon-192.png');

// PWA 512x512
await sharp(svgBuffer)
  .resize(512, 512)
  .png()
  .toFile(path.join(PUBLIC_DIR, 'favicon-512.png'));
console.log('Created public/favicon-512.png');

// Favicon ICO (16x16 and 32x32)
const png32 = await sharp(svgBuffer).resize(32, 32).png().toBuffer();
const png16 = await sharp(svgBuffer).resize(16, 16).png().toBuffer();

function createIco(images) {
  const count = images.length;
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(count, 4);

  let offset = 6 + count * 16;
  const directoryEntries = [];
  const imageBuffers = [];

  for (const img of images) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(img.width >= 256 ? 0 : img.width, 0);
    entry.writeUInt8(img.height >= 256 ? 0 : img.height, 1);
    entry.writeUInt8(0, 2);
    entry.writeUInt8(0, 3);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(img.data.length, 8);
    entry.writeUInt32LE(offset, 12);

    directoryEntries.push(entry);
    imageBuffers.push(img.data);
    offset += img.data.length;
  }

  return Buffer.concat([header, ...directoryEntries, ...imageBuffers]);
}

const icoBuffer = createIco([
  { width: 16, height: 16, data: png16 },
  { width: 32, height: 32, data: png32 }
]);

fs.writeFileSync(path.join(PUBLIC_DIR, 'favicon.ico'), icoBuffer);
console.log('Created public/favicon.ico');
