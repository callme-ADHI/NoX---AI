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
        background: '#0a0a0b',
        surface: '#111113',
        raised: '#17171a',
        border: '#222226',
        text: '#e8e8ec',
        muted: '#8a8a94',
        accent: {
          DEFAULT: '#7c9cff',
          hover: '#6889f0',
        },
        success: '#4ade80',
        warning: '#fbbf24',
        danger: '#f87171',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '10px',
        lg: '12px',
        md: '10px',
        sm: '8px',
      },
      transitionDuration: {
        DEFAULT: '150ms',
      },
    },
  },
  plugins: [],
};
