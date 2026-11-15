// ElevenLabs default voices: built into every account, so they work on the free plan through the API.
// (Voice Library voices, like main's Artemis voice fPIfC3elMLbN9tNwMXkw, need a paid plan.)
// Plain data, shared by the extension (panel menu), voice.js and prefs.js.
export const VOICES = [
    {key: 'bill', name: 'Bill', about: 'wise, calm, mature', id: 'pqHfZKP75CvOlQylNhV4'},
    {key: 'river', name: 'River', about: 'relaxed, neutral', id: 'SAz9YHcvj6GT2YYXdXww'},
    {key: 'sarah', name: 'Sarah', about: 'mature, reassuring', id: 'EXAVITQu4vr4xnSDxMaL'},
    {key: 'matilda', name: 'Matilda', about: 'knowledgeable, professional', id: 'XrExE9yKIg1WjnnlVkGX'},
    {key: 'alice', name: 'Alice', about: 'clear, British', id: 'Xb7hH8MSUJpSbSDYk0k2'},
    {key: 'george', name: 'George', about: 'warm British storyteller', id: 'JBFqnCBsd6RMkjVDRZzb'},
    {key: 'daniel', name: 'Daniel', about: 'steady British broadcaster', id: 'onwK4e9ZLuTAKqWW03F9'},
    {key: 'eric', name: 'Eric', about: 'smooth, trustworthy', id: 'cjVigY5qzO86Huf0OWal'},
    {key: 'brian', name: 'Brian', about: 'deep, resonant', id: 'nPczCjzI2devNBz1zQrb'},
    {key: 'lily', name: 'Lily', about: 'velvety, British', id: 'pFZP5JQG7iQjIQuC4Bku'},
    {key: 'bella', name: 'Bella', about: 'professional, bright', id: 'hpp4J3VqNfWAUOO0d1Us'},
    {key: 'roger', name: 'Roger', about: 'laid-back, casual', id: 'CwhRBWXzGAHq8TQ4Fs17'},
    {key: 'chris', name: 'Chris', about: 'charming, down-to-earth', id: 'iP95p4xoKVk53GoZ742B'},
    {key: 'will', name: 'Will', about: 'relaxed, optimistic', id: 'bIHbv24MWmeRgasZH58o'},
    {key: 'jessica', name: 'Jessica', about: 'playful, bright', id: 'cgSgspJ2msm6clMCkdW9'},
    {key: 'laura', name: 'Laura', about: 'enthusiastic, quirky', id: 'FGY2WhTYpPnrIDTdsKH5'},
    {key: 'adam', name: 'Adam', about: 'dominant, firm', id: 'pNInz6obpgDQGcFmaJgB'},
    {key: 'liam', name: 'Liam', about: 'energetic, young', id: 'TX3LPaxmHKxFdv7VOQHJ'},
    {key: 'charlie', name: 'Charlie', about: 'deep, Australian', id: 'IKne3meq5aSn9XLyUdCD'},
    {key: 'callum', name: 'Callum', about: 'husky', id: 'N2lVS1w4EtoT3dr4eOWO'},
    {key: 'harry', name: 'Harry', about: 'fierce', id: 'SOYHLrjzK2X1ezoPC6cr'},
    {key: 'system', name: 'System voice', about: 'offline, speech-dispatcher', id: null},
];

export const voiceByKey = (key) => VOICES.find((v) => v.key === key) ?? VOICES[0];
