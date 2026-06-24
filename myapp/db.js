const DB_NAME = "ChatAppDB";
const DB_VERSION = 1;
const STORE_NAME = "messages";

export function initDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    // This runs the first time the database is created
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        // Create a store for messages, using the unique message "_id" from FastAPI as the key
        db.createObjectStore(STORE_NAME, { keyPath: "_id" });
      }
    };

    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => reject(event.target.error);
  });
} 

// Save a single message object into IndexedDB
export async function saveMessageToDB(db, messageDocument) {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.put(messageDocument); // .put inserts or updates

    request.onsuccess = () => resolve();
    request.onerror = (event) => reject(event.target.error);
  });
}

// Get cached messages for a specific group chat
export async function getCachedMessages(db, groupId) {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, "readonly");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.getAll(); // Grab everything

    request.onsuccess = (event) => {
      const allMessages = event.target.result;
      // Filter the messages to only return the ones belonging to this group chat
      const filtered = allMessages.filter(msg => msg.group_id === groupId);
      // Sort them chronologically by timestamp
      filtered.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
      resolve(filtered);
    };
    request.onerror = (event) => reject(event.target.error);
  });
}

import { initDB, saveMessageToDB, getCachedMessages } from './db.js';

async function startChat(groupId, jwtToken) {
  // 1. Initialize IndexedDB
  const db = await initDB();

  // 2. Instantly render whatever we have saved offline
  const cachedMessages = await getCachedMessages(db, groupId);
  renderMessagesToUI(cachedMessages); // Your custom function to show text on screen

  // 3. Connect to your FastAPI WebSocket endpoint (Passing the JWT string!)
  const wsUrl = `ws://localhost:8000/ws/${groupId}?token=${jwtToken}`;
  const socket = new WebSocket(wsUrl);

  // 4. Handle incoming real-time traffic
  socket.onmessage = async (event) => {
    const messageDocument = JSON.parse(event.data);

    // Render it on screen right away
    appendMessageToUI(messageDocument);

    // CRITICAL: Cache it locally inside IndexedDB so it's saved forever!
    await saveMessageToDB(db, messageDocument);
  };

  // 5. Sending a message
  function sendMessage(text) {
    socket.send(text); // Just send raw text; FastAPI handles building the object!
  }
}