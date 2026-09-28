// Browser side of the SmallWebRTC call (FE-004). Media plumbing only: mic capture, one
// RTCPeerConnection, non-trickle SDP offer/answer, remote audio playout and a level meter
// for the ring. It never decides anything about the flow; the brain owns the call.
//
// Same shape as the Pipecat SmallWebRTC client (and our voice spike, agent/voice/
// spike_echo.py): audio sendrecv + a recvonly video transceiver, ICE gathered before the
// offer is sent, answer applied as-is. Signaling goes through the same-origin proxy
// (POST /api/session/call) because the agent's call endpoint also acquires the lease.

export type MicProblem = "denied" | "missing" | "unsupported";

export class MicError extends Error {
  constructor(readonly problem: MicProblem) {
    super(`microphone ${problem}`);
  }
}

/** Ask for the mic. Throws MicError so the driver can explain in text (EC-03). */
export async function openMic(): Promise<MediaStream> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) throw new MicError("unsupported");
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    const name = (e as { name?: string } | null)?.name;
    if (name === "NotFoundError" || name === "OverconstrainedError") throw new MicError("missing");
    if (name === "NotReadableError" || name === "AbortError") throw new MicError("missing");
    throw new MicError("denied"); // NotAllowedError / SecurityError / anything else
  }
}

export type LinkState = "connecting" | "connected" | "reconnecting" | "failed" | "closed";

export interface CallMedia {
  readonly offer: RTCSessionDescriptionInit;
  applyAnswer(answer: RTCSessionDescriptionInit): Promise<void>;
  setMuted(muted: boolean): void;
  onLink(cb: (s: LinkState) => void): void;
  onLevel(cb: (level: number) => void): void;
  close(): void;
}

/** ICE-002: hard cap on the browser's gather (was 3000). A relay candidate normally lands in
 * <300 ms and the offer goes 150 ms later; this only bounds a TURN server that never answers. */
const GATHER_TIMEOUT_MS = 2000;
/** LAT-001: once a TURN relay candidate is in, the offer is good enough to send (the agent's
 * leg relays through TURN too). Waiting for "complete" can mean waiting on the slowest
 * TURN transport (TCP/TLS) for up to GATHER_TIMEOUT_MS. */
const RELAY_GRACE_MS = 150;

/** Build the peer connection around the mic and gather ICE, ready to POST the offer. */
export async function prepareCall(mic: MediaStream, iceServers: RTCIceServer[] = []): Promise<CallMedia> {
  const pc = new RTCPeerConnection({ iceServers });
  let linkCb: (s: LinkState) => void = () => {};
  let levelCb: (l: number) => void = () => {};
  let closed = false;
  let audio: HTMLAudioElement | null = null;
  let meter: { ctx: AudioContext; timer: ReturnType<typeof setInterval> } | null = null;

  mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic));
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.onconnectionstatechange = () => {
    if (closed) return;
    const s = pc.connectionState;
    if (s === "connected") linkCb("connected");
    else if (s === "disconnected") linkCb("reconnecting");
    else if (s === "failed") linkCb("failed");
  };
  pc.ontrack = (e) => {
    if (e.track.kind !== "audio" || closed) return;
    const stream = e.streams[0] ?? new MediaStream([e.track]);
    audio ??= new Audio();
    audio.autoplay = true;
    audio.srcObject = stream;
    void audio.play().catch(() => {});
    meter ??= startMeter(stream, (l) => levelCb(l));
  };

  await pc.setLocalDescription(await pc.createOffer());
  await new Promise<void>((res) => {
    if (pc.iceGatheringState === "complete") return res();
    const done = () => pc.iceGatheringState === "complete" && res();
    pc.addEventListener("icegatheringstatechange", done);
    pc.addEventListener("icecandidate", (e) => {
      if (e.candidate && / typ relay /.test(` ${e.candidate.candidate} `)) setTimeout(res, RELAY_GRACE_MS);
    });
    setTimeout(res, GATHER_TIMEOUT_MS); // don't hang on a slow STUN/TURN server
  });
  const local = pc.localDescription!;

  return {
    offer: { sdp: local.sdp, type: local.type },
    async applyAnswer(answer) {
      await pc.setRemoteDescription(answer);
    },
    setMuted(muted) {
      mic.getAudioTracks().forEach((t) => (t.enabled = !muted));
    },
    onLink(cb) {
      linkCb = cb;
    },
    onLevel(cb) {
      levelCb = cb;
    },
    close() {
      if (closed) return;
      closed = true;
      if (meter) {
        clearInterval(meter.timer);
        void meter.ctx.close().catch(() => {});
      }
      if (audio) {
        audio.pause();
        audio.srcObject = null;
      }
      mic.getTracks().forEach((t) => t.stop());
      pc.close();
    },
  };
}

/** Stop a mic stream that never made it into a call. */
export function releaseMic(mic: MediaStream | null) {
  mic?.getTracks().forEach((t) => t.stop());
}

function startMeter(stream: MediaStream, cb: (level: number) => void) {
  const ctx = new AudioContext();
  const an = ctx.createAnalyser();
  an.fftSize = 512;
  ctx.createMediaStreamSource(stream).connect(an);
  const buf = new Uint8Array(an.fftSize);
  const timer = setInterval(() => {
    an.getByteTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += ((v - 128) / 128) ** 2;
    cb(Math.min(1, Math.sqrt(sum / buf.length) * 4));
  }, 100);
  return { ctx, timer };
}
