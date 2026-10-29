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
        background: '#000000',
        nightSky: '#030305',
        surface: 'rgba(10, 10, 14, 0.72)',
        raised: 'rgba(18, 18, 24, 0.88)',
        border: 'rgba(255, 255, 255, 0.08)',
        text: '#F8FAFC',
        muted: '#94A3B8',
        accent: {
          DEFAULT: '#38BDF8',
          hover: '#7DD3FC',
          violet: '#818CF8',
          gold: '#FCD34D',
        },
        success: '#34D399',
        warning: '#FBBF24',
        danger: '#FB7185',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '10px',
        lg: '14px',
        md: '10px',
        sm: '8px',
      },
      backdropBlur: {
        xs: '2px',
        glass: '16px',
        xl: '20px',
      },
      boxShadow: {
        'glass': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.08), 0 8px 32px 0 rgba(0, 0, 0, 0.55)',
        'glass-raised': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.14), 0 16px 48px 0 rgba(0, 0, 0, 0.75)',
        'glow-cyan': '0 0 24px -3px rgba(56, 189, 248, 0.35)',
        'glow-starlight': '0 0 20px -2px rgba(255, 255, 255, 0.2)',
      },
    },
  },
  plugins: [],
};
