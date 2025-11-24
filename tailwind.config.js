module.exports = {
  content: [
    "./templates/**/*.html",
    "./**/*.html",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
      },
      colors: {
        blue: {
          50:  "#FFF5E6",
          100: "#FFEACC",
          200: "#FFD699",
          300: "#FFC266",
          400: "#FFAD33",
          500: "#FF9900",
          600: "#FF7A00",
          700: "#CC6200",
          800: "#994900",
          900: "#663100",
        },
      },
    },
  },
  plugins: [],
}
