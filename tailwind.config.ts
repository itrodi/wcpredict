import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        pitch: {
          950: "#07120c",
          900: "#0b1a12",
          800: "#11241a",
          700: "#1a3526",
        },
        accent: {
          DEFAULT: "#34d399",
          dim: "#10b981",
        },
      },
    },
  },
  plugins: [],
};

export default config;
