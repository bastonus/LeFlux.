// Test the plugin locally in Node.js
const fs = require('fs');
const path = require('path');

globalThis._settings = {
  c411_apikey: "",
  torr9_passkey: "",
  tr4ker_apikey: "",
  gemini_apikey: "",
  max_size_gb: 50,
  proxy_base: "http://localhost:8082/plugin/proxy"
};

// Also mock _tmdbApiKey as NuvioTV does
globalThis._tmdbApiKey = "";

// Load the compiled provider file
const providerPath = '/home/azandikka/Documents/Frenchio/Frenchio/frenchio-nuvio-plugin/providers/frenchio-p2p.js';
if (!fs.existsSync(providerPath)) {
  console.error("Provider file not found at " + providerPath + ". Build it first!");
  process.exit(1);
}

const { getStreams } = require(providerPath);

async function runTest() {
  console.log("--- Test 1: Movie (The Gentlemen, tmdbId 522627) ---");
  try {
    const streams = await getStreams("522627", "movie", null, null);
    console.log("Streams found:", streams.length);
    streams.forEach(s => {
      console.log(`- ${s.name}: ${s.description} -> ${s.url ? s.url.substring(0, 60) + '...' : 'no url'}`);
    });
  } catch (err) {
    console.error("Error in Test 1:", err);
  }

  console.log("\n--- Test 2: TV Series (The Gentlemen S01E03, tmdbId 236235) ---");
  try {
    const streams = await getStreams("236235", "tv", 1, 3);
    console.log("Streams found:", streams.length);
    streams.forEach(s => {
      console.log(`- ${s.name}: ${s.description} -> ${s.url ? s.url.substring(0, 60) + '...' : 'no url'}`);
    });
  } catch (err) {
    console.error("Error in Test 2:", err);
  }
}

runTest();
