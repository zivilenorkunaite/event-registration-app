const sharp = require('sharp');

// Label 80mm x 50mm at 203 DPI
const LABEL_WIDTH_PX = Math.round((80 / 25.4) * 203);   // ~639
const LABEL_HEIGHT_PX = Math.round((50 / 25.4) * 203);  // ~400

/**
 * Generate name tag as PNG buffer for Niimbot B3S (80mm x 50mm landscape).
 * @param {Object} opts - { groupName, location, name, company }
 * @returns {Promise<Buffer>} PNG buffer
 */
async function generateNameTagImage(opts) {
  const groupName = (opts.groupName || 'Energy & Utilities Data Connect').toUpperCase();
  const location = (opts.location || 'Sydney').toUpperCase();
  const name = (opts.name || 'YOUR NAME').toUpperCase();
  const company = opts.company || 'Company';

  const w = LABEL_WIDTH_PX;
  const h = LABEL_HEIGHT_PX;
  const padding = 15;
  const leftW = Math.round(w * 0.28);
  const centerW = Math.round(w * 0.44);
  const rightW = w - leftW - centerW - 2; // minus 2 for dividers
  const divX1 = leftW + 1;
  const divX2 = leftW + centerW + 2;

  const svg = `
<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">
  <rect width="${w}" height="${h}" fill="white"/>
  <g font-family="Arial, sans-serif" fill="#1a1a1a">
    <text x="${padding}" y="${h/2 - 18}" text-anchor="start" dominant-baseline="middle" font-size="9" font-weight="700" letter-spacing="0.5">${escapeXml(groupName)}</text>
    <text x="${padding}" y="${h/2}" text-anchor="start" dominant-baseline="middle" font-size="11" font-weight="700">${escapeXml(location)}</text>
    <line x1="${divX1}" y1="${padding}" x2="${divX1}" y2="${h - padding}" stroke="#ddd" stroke-width="1"/>
    <text x="${leftW + centerW/2 + 1}" y="${h/2}" text-anchor="middle" dominant-baseline="middle" font-size="36" font-weight="700" letter-spacing="1">${escapeXml(name)}</text>
    <line x1="${divX2}" y1="${padding}" x2="${divX2}" y2="${h - padding}" stroke="#ddd" stroke-width="1"/>
    <text x="${w - padding}" y="${h/2}" text-anchor="end" dominant-baseline="middle" font-size="12">${escapeXml(company)}</text>
  </g>
</svg>`;

  const png = await sharp(Buffer.from(svg))
    .png()
    .toBuffer();

  return png;
}

function escapeXml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

module.exports = { generateNameTagImage, LABEL_WIDTH_PX, LABEL_HEIGHT_PX };
