const sharp = require("sharp");

// Label 80mm x 50mm at 203 DPI
const LABEL_WIDTH_PX = Math.round((80 / 25.4) * 203); // ~639
const LABEL_HEIGHT_PX = Math.round((50 / 25.4) * 203); // ~400

// Font size configuration for responsive scaling
const FONT_SIZES = {
  name: {
    base: 70,
    thresholds: [
      { length: 8, size: 55 },
      { length: 12, size: 45 },
      { length: 15, size: 35 },
    ],
  },
  company: {
    base: 22,
    thresholds: [
      { length: 15, size: 20 },
      { length: 20, size: 18 },
      { length: 25, size: 16 },
    ],
  },
  group: {
    base: 15,
    thresholds: [
      { length: 25, size: 14 },
      { length: 30, size: 13 },
      { length: 35, size: 12 },
      { length: 40, size: 11 },
    ],
  },
  location: {
    base: 16,
    thresholds: [
      { length: 25, size: 15 },
      { length: 30, size: 14 },
      { length: 35, size: 13 },
      { length: 40, size: 12 },
    ],
  },
};

const SVG_CONFIG = {
  padding: 10,
  dividerStroke: "#ddd",
  dividerWidth: 1,
  textColor: "#1a1a1a",
  fontFamily: "Arial, sans-serif",
};

/**
 * Generate name tag SVG string for Niimbot B3S (80mm x 50mm landscape).
 * @param {Object} opts - { groupName, location, name, company }
 * @returns {string} SVG string
 */
function generateNameTagSvg(opts) {
  const groupName = (
    opts.groupName || "Energy & Utilities Data Connect"
  ).toUpperCase();
  const location = (opts.location || "Sydney").toUpperCase();
  const name = (opts.name || "YOUR NAME").toUpperCase();
  const company = opts.company || "Company";

  const w = LABEL_WIDTH_PX;
  const h = LABEL_HEIGHT_PX;
  const padding = SVG_CONFIG.padding;

  // Vertical sections: top (event), middle (name), bottom (company)
  const topH = Math.round(h * 0.25);
  const middleH = Math.round(h * 0.5);
  const bottomH = h - topH - middleH;

  const divY1 = topH;
  const divY2 = topH + middleH;

  // Helper function to calculate responsive font size
  const calculateFontSize = (text, config) => {
    let size = config.base;
    for (const threshold of config.thresholds) {
      if (text.length > threshold.length) {
        size = threshold.size;
      }
    }
    return size;
  };

  // Calculate font sizes based on text length to maximize readability
  // Hierarchy: Name (largest) > Company > Event Description (smallest)
  const nameFontSize = calculateFontSize(name, FONT_SIZES.name);
  const companyFontSize = calculateFontSize(company, FONT_SIZES.company);
  const groupFontSize = calculateFontSize(groupName, FONT_SIZES.group);
  const locationFontSize = calculateFontSize(location, FONT_SIZES.location);

  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">
  <rect width="${w}" height="${h}" fill="white"/>
  <g font-family="${SVG_CONFIG.fontFamily}" fill="${SVG_CONFIG.textColor}">
    <!-- Top section: Event name and location -->
    <text x="${w / 2}" y="${topH / 2 - 9}" text-anchor="middle" dominant-baseline="middle" font-size="${groupFontSize}" font-weight="700" letter-spacing="0.3">${escapeXml(groupName)}</text>
    <text x="${w / 2}" y="${topH / 2 + 9}" text-anchor="middle" dominant-baseline="middle" font-size="${locationFontSize}" font-weight="700">${escapeXml(location)}</text>
    
    <!-- Top divider -->
    <line x1="${SVG_CONFIG.padding}" y1="${divY1}" x2="${w - SVG_CONFIG.padding}" y2="${divY1}" stroke="${SVG_CONFIG.dividerStroke}" stroke-width="${SVG_CONFIG.dividerWidth}"/>
    
    <!-- Middle section: Name (largest for readability) -->
    <text x="${w / 2}" y="${topH + middleH / 2}" text-anchor="middle" dominant-baseline="middle" font-size="${nameFontSize}" font-weight="700" letter-spacing="0.5">${escapeXml(name)}</text>
    
    <!-- Bottom divider -->
    <line x1="${SVG_CONFIG.padding}" y1="${divY2}" x2="${w - SVG_CONFIG.padding}" y2="${divY2}" stroke="${SVG_CONFIG.dividerStroke}" stroke-width="${SVG_CONFIG.dividerWidth}"/>
    
    <!-- Bottom section: Company -->
    <text x="${w / 2}" y="${divY2 + bottomH / 2}" text-anchor="middle" dominant-baseline="middle" font-size="${companyFontSize}" font-weight="600">${escapeXml(company)}</text>
  </g>
</svg>`;

  return svg;
}

/**
 * Generate name tag as PNG buffer for Niimbot B3S (80mm x 50mm landscape).
 * @param {Object} opts - { groupName, location, name, company }
 * @returns {Promise<Buffer>} PNG buffer
 */
async function generateNameTagImage(opts) {
  const svg = generateNameTagSvg(opts);

  const png = await sharp(Buffer.from(svg)).png().toBuffer();

  return png;
}

/**
 * Generate both SVG and PNG for a name tag in one operation.
 * @param {Object} opts - { groupName, location, name, company }
 * @returns {Promise<{svg: string, png: Buffer}>} Object with both SVG string and PNG buffer
 */
async function generateNameTag(opts) {
  const svg = generateNameTagSvg(opts);
  const png = await sharp(Buffer.from(svg)).png().toBuffer();

  return { svg, png };
}

function escapeXml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

module.exports = {
  generateNameTagImage,
  generateNameTag,
  generateNameTagSvg,
  LABEL_WIDTH_PX,
  LABEL_HEIGHT_PX,
};
