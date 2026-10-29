/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: '#05060F',
        nightSky: '#0B1026',
        surface: 'rgba(16, 22, 48, 0.70)',
        raised: 'rgba(24, 32, 68, 0.85)',
        border: 'rgba(160, 180, 255, 0.15)',
        text: '#E6EAFF',
        muted: '#8A94C4',
        accent: {
          DEFAULT: '#C9D4FF',
          hover: '#E2E8FF',
          violet: '#7C6CFF',
        },
        success: '#4FE3C1',
        warning: '#F6C36B',
        danger: '#FF6B8A',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '10px',
        lg: '12px',
        md: '10px',
        sm: '8px',
      },
      backdropBlur: {
        xs: '2px',
        glass: '12px',
      },
    },
  },
  plugins: [],
};
