// build.js — Transpile src/frenchio-p2p/index.js → providers/frenchio-p2p.js
// Nuvio attend un module CJS qui exporte { getStreams }

const esbuild = require('esbuild');

esbuild.build({
  entryPoints: ['src/frenchio-p2p/index.js'],
  bundle: true,
  outfile: 'providers/frenchio-p2p.js',
  platform: 'node',        // module.exports compatible
  format: 'cjs',           // CommonJS — Nuvio/Hermes charge avec require()
  target: ['es2015'],      // Force lowering de tout async/await → generators
  supported: {
    'async-await': false,
  },
  minify: false,
  banner: {
    js: '// LeFlux. P2P — Nuvio Provider Plugin v1.0.0\n// Built for Hermes (React Native)\n// Do not edit — edit src/frenchio-p2p/index.js and run: npm run build\n',
  },
}).then(() => {
  console.log('✅ Build complete → providers/frenchio-p2p.js');
}).catch((err) => {
  console.error('❌ Build failed:', err);
  process.exit(1);
});
