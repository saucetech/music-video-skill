/**
 * Sample LYRICS_DATA for testing all templates.
 *
 * Include this file before the template HTML, or paste into the browser console.
 *
 * Usage:
 *   <script src="sample-data.js"><\/script>
 *   Then open any template HTML file. Set window.FRAME_TIME to scrub.
 *
 * For auto-playback, use:
 *   let start = performance.now();
 *   function tick() {
 *     window.FRAME_TIME = (performance.now() - start) / 1000;
 *     requestAnimationFrame(tick);
 *   }
 *   tick();
 */

window.LYRICS_DATA = {
  // Beat timestamps for beat-pulse template (120 BPM)
  bpm: 120,
  beats: [
    0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
    4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5,
    8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0, 11.5,
    12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 15.5,
    16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5,
    20.0, 20.5, 21.0, 21.5, 22.0, 22.5, 23.0, 23.5,
    24.0
  ],

  lines: [
    {
      text: "I walk through the fire",
      startTime: 0.5,
      endTime: 3.8,
      words: [
        { text: "I",       startTime: 0.5,  endTime: 0.7,  role: "support",   emphasis: 0.1 },
        { text: "walk",    startTime: 0.8,  endTime: 1.3,  role: "key",       emphasis: 0.7 },
        { text: "through", startTime: 1.4,  endTime: 1.9,  role: "support",   emphasis: 0.2 },
        { text: "the",     startTime: 2.0,  endTime: 2.2,  role: "support",   emphasis: 0.1 },
        { text: "fire",    startTime: 2.3,  endTime: 3.5,  role: "emotional", emphasis: 1.0 }
      ]
    },
    {
      text: "burning everything I know",
      startTime: 4.2,
      endTime: 7.5,
      words: [
        { text: "burning",    startTime: 4.2,  endTime: 4.9,  role: "emotional", emphasis: 0.9 },
        { text: "everything", startTime: 5.0,  endTime: 5.8,  role: "key",       emphasis: 0.8 },
        { text: "I",          startTime: 5.9,  endTime: 6.1,  role: "support",   emphasis: 0.1 },
        { text: "know",       startTime: 6.2,  endTime: 7.2,  role: "key",       emphasis: 0.7 }
      ]
    },
    {
      text: "but in the ashes something grows",
      startTime: 8.0,
      endTime: 12.0,
      words: [
        { text: "but",       startTime: 8.0,  endTime: 8.3,  role: "support",   emphasis: 0.1 },
        { text: "in",        startTime: 8.4,  endTime: 8.6,  role: "support",   emphasis: 0.1 },
        { text: "the",       startTime: 8.7,  endTime: 8.9,  role: "support",   emphasis: 0.1 },
        { text: "ashes",     startTime: 9.0,  endTime: 9.7,  role: "emotional", emphasis: 0.9 },
        { text: "something", startTime: 9.8,  endTime: 10.5, role: "accent",    emphasis: 0.5 },
        { text: "grows",     startTime: 10.6, endTime: 11.7, role: "key",       emphasis: 1.0 }
      ]
    },
    {
      text: "rising from the cold",
      startTime: 12.5,
      endTime: 15.5,
      words: [
        { text: "rising", startTime: 12.5, endTime: 13.3, role: "key",       emphasis: 0.9 },
        { text: "from",   startTime: 13.4, endTime: 13.7, role: "support",   emphasis: 0.2 },
        { text: "the",    startTime: 13.8, endTime: 14.0, role: "support",   emphasis: 0.1 },
        { text: "cold",   startTime: 14.1, endTime: 15.2, role: "emotional", emphasis: 0.8 }
      ]
    },
    {
      text: "we are the light they cannot hold",
      startTime: 16.0,
      endTime: 20.5,
      words: [
        { text: "we",     startTime: 16.0, endTime: 16.3, role: "support",   emphasis: 0.2 },
        { text: "are",    startTime: 16.4, endTime: 16.7, role: "support",   emphasis: 0.1 },
        { text: "the",    startTime: 16.8, endTime: 17.0, role: "support",   emphasis: 0.1 },
        { text: "light",  startTime: 17.1, endTime: 18.0, role: "key",       emphasis: 1.0 },
        { text: "they",   startTime: 18.1, endTime: 18.4, role: "support",   emphasis: 0.1 },
        { text: "cannot", startTime: 18.5, endTime: 19.0, role: "accent",    emphasis: 0.4 },
        { text: "hold",   startTime: 19.1, endTime: 20.2, role: "emotional", emphasis: 0.8 }
      ]
    },
    {
      text: "forever",
      startTime: 21.0,
      endTime: 24.0,
      words: [
        { text: "forever", startTime: 21.0, endTime: 23.5, role: "emotional", emphasis: 1.0 }
      ]
    }
  ]
};

// Auto-playback helper
window.startPlayback = function() {
  var start = performance.now();
  function tick() {
    window.FRAME_TIME = (performance.now() - start) / 1000;
    window.renderFrame(window.FRAME_TIME);
    requestAnimationFrame(tick);
  }
  tick();
};
