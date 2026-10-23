// Contextual help dialogs (static, trusted markup — see openDialog in ui.js).
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ═══════════════════════════════════════════════════════
//  HELP SYSTEM
// ═══════════════════════════════════════════════════════
const HELP = {
  map: {
    title: '🗺️ Map — How to Use',
    body: `
      <p>The map shows interesting and hidden places near you. Places are categorized (ruins, bunkers, abandoned, etc.) and scored by confidence.</p>
      <ul>
        <li><strong>📍 Locate</strong> — centers the map on your GPS position</li>
        <li><strong>🌫️ Fog of War</strong> — unvisited areas appear as dark fog; explore to reveal them!</li>
        <li><strong>🔄 Refresh</strong> — reload places for the current map view</li>
        <li><strong>🔍 Search</strong> — type natural language queries like "abandoned buildings near water"</li>
        <li><strong>🕰️ History</strong> — overlay historical maps from different eras to compare changes</li>
        <li><strong>🔥 Heatmap</strong> — shows everywhere you have checked in (your "life map")</li>
        <li><strong>🌗 Theme</strong> — switch light / dark / ☀️ sun (high contrast for bright daylight); more in ⚙️ Settings</li>
      </ul>
      <div class="help-tip"><strong>Place of the day:</strong> the card at the top-left suggests one unusual place near you. Dismiss it with ✕ — a new one appears tomorrow.</div>
      <div class="help-tip"><strong>Filters:</strong> Tap the bottom panel to expand it. Use presets (Urban Explorer, Nature, All) or toggle individual categories. Adjust radius (0.5–50 km) and confidence sliders.</div>
      <div class="help-tip"><strong>Place Cards:</strong> Click any marker to see details. The ✨ number is how unusual the place is, the chips say why, and you get a Wikipedia summary and photos when available. Ask the AI about a place under "Ask about this place". Use 📍 to check in and earn XP.</div>
      <div class="help-tip"><strong>Outdoors:</strong> turn on 🔋 Low-power mode in ⚙️ Settings for long hikes — GPS is polled less and animations stop. Tracking pauses while the app is in the background. Places and map tiles you viewed stay available offline.</div>
      <div class="help-tip"><strong>Keyboard:</strong> Tab to navigate controls, Enter/Space to activate. Use +/- to zoom the map.</div>
    `
  },
  chat: {
    title: '🤖 AI Chat — How to Use',
    body: `
      <p>Chat with an AI assistant to discover places using natural language. The AI understands your mood, preferences, and location context.</p>
      <ul>
        <li>Ask in <strong>any language</strong> — Russian, English, German, etc.</li>
        <li>The AI remembers conversation context within your session</li>
        <li>Found places appear as <strong>clickable cards</strong> — tap to see on map</li>
        <li>Use <strong>suggested questions</strong> below the chat for quick discovery</li>
        <li>Tap <strong>🎤</strong> to dictate (if your browser supports it) and <strong>🔈</strong> to have replies read aloud — both are optional and off until you use them</li>
      </ul>
      <div class="help-tip"><strong>Try:</strong> "Покажи заброшенные места рядом с водой" / "Find mysterious ruins within 5km" / "I want something creepy and photogenic"</div>
      <div class="help-tip"><strong>Tip:</strong> The more detail you give (mood, distance, terrain), the better the recommendations.</div>
    `
  },
  journal: {
    title: '📓 Journal — How to Use',
    body: `
      <p>Track your explorations with visits, trips, notes, and detailed statistics.</p>
      <ul>
        <li><strong>Visits</strong> — check in at places via the map (📍 button on place cards)</li>
        <li><strong>Trips</strong> — group visits into trips (e.g., "Montenegro Weekend"). Create via the Trips tab.</li>
        <li><strong>Notes</strong> — add text and rate visits on 4 dimensions: Atmosphere, Accessibility, Photogenic, Uniqueness (1-5 stars each)</li>
        <li><strong>Stats</strong> — view total distance explored, hours, categories, and your personal heatmap</li>
        <li><strong>Export</strong> — save trips as Markdown (for blogs) or JSON (for data analysis)</li>
      </ul>
      <div class="help-tip"><strong>Getting started:</strong> Go to the Map, click a place marker, then press 📍 Check In. Your visit will appear here!</div>
    `
  },
  routes: {
    title: '🛤️ Routes — How to Use',
    body: `
      <p>Build optimized routes between interesting places with export to GPS devices.</p>
      <ul>
        <li><strong>Explore Route</strong> — enter origin/destination as "lat, lng" (e.g., "42.441, 18.771"), set time budget and categories</li>
        <li><strong>Circular Walk</strong> — generate a loop route from your location. Set duration (1-8 hours) and categories.</li>
        <li><strong>Loop</strong> — "a loop of N hours on foot or by bike": pick duration, number of stops and categories; the route is drawn on the map and can be exported like any other</li>
        <li><strong>My Routes</strong> — routes you saved on this device</li>
        <li><strong>GPX Export</strong> — download for Garmin, Strava, Komoot (the export is built by the server, so it needs a connection)</li>
        <li><strong>KML Export</strong> — download for Google Earth, Google Maps</li>
        <li><strong>Route Story</strong> — AI generates a narrative connecting all places in your route</li>
      </ul>
      <div class="help-tip"><strong>Coordinates:</strong> Enter as "latitude, longitude" separated by comma. Use the Map's 📍 button to find your coordinates.</div>
      <div class="help-tip"><strong>Tip:</strong> Select fewer categories for focused routes, more for variety.</div>
    `
  },
  gamification: {
    title: '🏆 Gamification — How to Use',
    body: `
      <p>Level up your explorer profile by visiting places and completing challenges!</p>
      <ul>
        <li><strong>XP</strong> — earned by checking in (+10 XP), writing notes (+5 XP), and unlocking achievements (+25-100 XP)</li>
        <li><strong>Levels</strong> — each level unlocks a new explorer title (Novice → Scout → Pioneer → Legend)</li>
        <li><strong>Achievements</strong> — 26 badges for milestones. Some are hidden — discover them by exploring!</li>
        <li><strong>Leaderboard</strong> — compete with other explorers for top positions</li>
        <li><strong>Fog Stats</strong> — see exploration percentage per region (city, country)</li>
        <li><strong>🔥 Streak</strong> — consecutive days with a check-in; the 60-day strip shows your active days</li>
        <li><strong>📚 Collections</strong> — progress per category near you, e.g. 10/47 abandoned places visited</li>
      </ul>
      <div class="help-tip"><strong>Quick XP:</strong> Check in at 3 different categories in one day for a bonus achievement!</div>
    `
  },
  community: {
    title: '👥 Community — How to Use',
    body: `
      <p>Share discoveries, write reviews, and connect with fellow explorers.</p>
      <ul>
        <li><strong>Submit Places</strong> — add places you've found. Include name, description, coordinates, and category. Places go through moderation.</li>
        <li><strong>Reviews</strong> — write reviews with 1-5 star ratings. Vote on others' reviews (👍/👎).</li>
        <li><strong>Published Routes</strong> — browse routes shared by the community, with ratings</li>
        <li><strong>Follow</strong> — follow other explorers to see their latest discoveries</li>
        <li><strong>Karma</strong> — earn karma by submitting quality places, helpful reviews, and getting upvotes</li>
      </ul>
      <div class="help-tip"><strong>Getting started:</strong> Submit your first place to earn your "Contributor" achievement!</div>
    `
  },
  offline: {
    title: '📴 Offline Mode — How to Use',
    body: `
      <p>Download maps and place data for areas without internet — perfect for remote exploration.</p>
      <ul>
        <li><strong>Download Tiles</strong> — enter a bounding box (min/max lat/lng) and zoom levels to cache map tiles</li>
        <li><strong>Cached Places</strong> — automatically cache visited place info including AI descriptions</li>
        <li><strong>Sync Queue</strong> — check-ins and notes made offline are queued and synced when back online</li>
        <li><strong>Storage</strong> — monitor disk usage for tiles and cached data</li>
      </ul>
      <div class="help-tip"><strong>Recommended:</strong> Download tiles at zoom levels 10-15 for a good balance of detail vs. storage (typically 50-200 MB per city).</div>
      <div class="help-tip"><strong>Tip:</strong> Cache places before a trip so you have descriptions and ratings available offline.</div>
    `
  }
};

function showHelp(section) {
  const h = HELP[section];
  if (!h) return;
  openDialog(html`<h3 id="dialog-title">${h.title}</h3>${raw(h.body)}<button class="btn btn-primary btn-sm w-100 mt-16" data-action="close-dialog">Got it!</button>`);
}

registerActions({ help: (el) => showHelp(el.dataset.topic) });
