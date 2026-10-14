/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eef4ff', 100: '#dce7fd', 500: '#2f6df6', 600: '#2459d6', 700: '#1d48ad',
        },
      },
    },
  },
  plugins: [],
}
