// SPDX-License-Identifier: Apache-2.0
// The site's Tailwind config (ported from projects/g6_web). `npm run css:build` rewrites ../static/css/main.css.
const defaultTheme = require("tailwindcss/defaultTheme");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["../templates/**/*.html", "../static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        g6: {
          black: "#000000",
          dark: "#0a0a0a",
          purple: { dark: "#1a0a2e", mid: "#2d1b69" },
          indigo: "#6366f1",
          violet: "#8b5cf6",
          pink: "#ec4899",
          green: "#00ff88",
          yellow: "#ffd54a",
        },
      },
      fontFamily: {
        sans: ["Inter", ...defaultTheme.fontFamily.sans],
        mono: ["Geist Mono", ...defaultTheme.fontFamily.mono],
      },
    },
  },
  plugins: [],
};
