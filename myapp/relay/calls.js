// # Runs on the call starter's device (Desktop App or Browser — identical either way,
// # since both are just JS in a Chromium-based WebRTC context).

async function startCallAndDistributeKey(groupId, memberUserIds) {
  // One random 256-bit key for this call's audio, generated locally —
  // NEVER sent to the relay or the SFU in plaintext.
  const callKey = crypto.getRandomValues(new Uint8Array(32));

  const { call_id } = await api.post(`/groups/${groupId}/calls/start`);

  // For each OTHER participant, look up their long-term identity public key
  // from the relay's directory (Section: User.identity_public_key), derive a
  // pairwise shared secret via ECDH, and use THAT to encrypt just this one
  // 32-byte call key — a "sender key" distribution, same pattern Signal uses
  // for group messaging keys.
  for (const memberUserId of memberUserIds) {
    const theirPublicKey = await api.get(`/users/${memberUserId}/identity_key`);
    const pairwiseKey = await deriveSharedKey(myPrivateKey, theirPublicKey);
    const encryptedCallKey = await encrypt(callKey, pairwiseKey);

    // Sent over the EXISTING encrypted relay channel as just another event —
    // the relay forwards this opaque blob exactly like a chat message.
    await sendRelayEvent(memberUserId, { type: "call.key_exchange", call_id, encrypted_call_key: encryptedCallKey });
  }

  await joinCallMedia(call_id, callKey);
}


// Runs on EVERY participant — encrypts outgoing frames before they leave
// the browser/desktop for the SFU, decrypts incoming frames after they arrive.
// Uses WebRTC's Encoded Transform API (Insertable Streams), supported in
// Chromium — which covers both the browser client AND the Electron desktop app.

function attachE2EEncryption(sender, callKey) {
  const senderStreams = sender.createEncodedStreams();
  const transform = new TransformStream({
    async transform(encodedFrame, controller) {
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        callKey,
        encodedFrame.data
      );
      // Prepend the IV so the receiver can decrypt — SFU forwards this
      // blob unmodified, having no idea it's ciphertext vs. real audio data.
      encodedFrame.data = concatBuffers(iv, ciphertext);
      controller.enqueue(encodedFrame);
    },
  });
  senderStreams.readable.pipeThrough(transform).pipeTo(senderStreams.writable);
}

function attachE2EDecryption(receiver, callKey) {
  const receiverStreams = receiver.createEncodedStreams();
  const transform = new TransformStream({
    async transform(encodedFrame, controller) {
      const iv = encodedFrame.data.slice(0, 12);
      const ciphertext = encodedFrame.data.slice(12);
      encodedFrame.data = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, callKey, ciphertext);
      controller.enqueue(encodedFrame);
    },
  });
  receiverStreams.readable.pipeThrough(transform).pipeTo(receiverStreams.writable);
}