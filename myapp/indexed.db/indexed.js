/**
 * Browser-side local cache, backed by IndexedDB.
 *
 * Two object stores:
 *   - "messages": chat history, append-only log
 *   - "files":    editor/code snapshots, append-only log of changes
 *
 * On page load: read everything already cached locally (instant, no
 * network needed), THEN ask the server for only what's new since the
 * counts we already have, and append that in.
 */

const DB_NAME = "DesktopSyncDB";
const DB_VERSION = 1;

let dbPromise = null;

function openDB() {
  if (dbPromise) return dbPromise; // only open the connection once, reuse it

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    // Runs only the FIRST time this DB version is opened on this browser
    // (or when you bump DB_VERSION). This is where the schema is defined.
    request.onupgradeneeded = (event) => {
      const db = event.target.result;

      if (!db.objectStoreNames.contains("messages")) {
        // autoIncrement gives each item a local numeric key (1, 2, 3...)
        // in insertion order -- that order matches the server's list
        // index, which is what makes "since_messages=count" line up.
        db.createObjectStore("messages", { autoIncrement: true });
      }
      if (!db.objectStoreNames.contains("files")) {
        db.createObjectStore("files", { autoIncrement: true });
      }
    };

    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => reject(event.target.error);
  });

  return dbPromise;
}

/** Wraps IndexedDB's callback-style transactions in a Promise. */
function runTransaction(db, storeName, mode, fn) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    const result = fn(store);
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
  });
}

/** How many items we already have locally -- this becomes the "since" cursor. */
async function getLocalCount(db, storeName) {
  return runTransaction(db, storeName, "readonly", (store) => {
    return new Promise((resolve, reject) => {
      const req = store.count();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }).then((countPromise) => countPromise); // count() itself returns a promise-wrapped request above
}

/** Append new items onto the end of a store, preserving order. */
async function appendItems(db, storeName, items) {
  if (!items || items.length === 0) return;
  return runTransaction(db, storeName, "readwrite", (store) => {
    for (const item of items) {
      store.add(item); // autoIncrement key, so this always appends
    }
  });
}

/** Read everything currently cached locally, in order. */
async function getAll(db, storeName) {
  return runTransaction(db, storeName, "readonly", (store) => {
    return new Promise((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }).then((p) => p);
}

/**
 * Main entry point. Call this on login / page load.
 * Returns the FULL local dataset (old + newly synced), read from IndexedDB.
 */
async function syncWithServer(desktopId) {
  const db = await openDB();

  // 1. Load what we already have locally FIRST, so the UI can render
  //    immediately without waiting on the network at all.
  const localMessages = await getAll(db, "messages");
  const localFiles = await getAll(db, "files");

  // 2. Ask the server for only what's new past our local counts.
  const sinceMessages = localMessages.length;
  const sinceFiles = localFiles.length;

  const response = await fetch(
    `/api/login/${desktopId}?since_messages=${sinceMessages}&since_files=${sinceFiles}`,
    { method: "POST" }
  );
  const data = await response.json();

  // 3. Append only the NEW items into IndexedDB -- we never re-write
  //    what's already cached, so this stays cheap even with a huge history.
  await appendItems(db, "messages", data.messages);
  await appendItems(db, "files", data.files);

  return {
    status: data.status, // "live" | "cached" | "no_data"
    messages: [...localMessages, ...data.messages],
    files: [...localFiles, ...data.files],
  };
}

// Example usage in your app:
//
//   const { status, messages, files } = await syncWithServer("desktop_123");
//   renderChat(messages);
//   renderEditor(files);
//   if (status === "cached") showBanner("Desktop offline -- showing last known state");

export { syncWithServer, openDB, getAll };