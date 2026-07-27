type AudioContextConstructor = typeof AudioContext;

type WindowWithWebkitAudio = Window & {
  webkitAudioContext?: AudioContextConstructor;
};

export class BrowserVoiceRecorder {
  private audioContext: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;
  private stream: MediaStream | null = null;
  private chunks: Float32Array[] = [];
  private inputSampleRate = 48000;

  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前环境不能访问麦克风。");
    }

    this.stopTracks();
    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    const AudioContextImpl = window.AudioContext ?? windowWithWebkit().webkitAudioContext;
    if (!AudioContextImpl) {
      throw new Error("当前环境不支持 Web Audio。");
    }

    this.audioContext = new AudioContextImpl();
    this.inputSampleRate = this.audioContext.sampleRate;
    this.source = this.audioContext.createMediaStreamSource(this.stream);
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
    this.sink = this.audioContext.createGain();
    this.sink.gain.value = 0;
    this.processor.onaudioprocess = (event) => {
      const channel = event.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(channel));
    };
    this.source.connect(this.processor);
    this.processor.connect(this.sink);
    this.sink.connect(this.audioContext.destination);
  }

  async stop(): Promise<string> {
    this.disconnectNodes();
    const audio = concatenateFloat32(this.chunks);
    this.chunks = [];
    this.stopTracks();
    if (this.audioContext) {
      await this.audioContext.close();
      this.audioContext = null;
    }

    if (audio.length === 0) {
      throw new Error("没有录到声音。");
    }

    const resampled = resampleLinear(audio, this.inputSampleRate, 16000);
    const wav = encodePcm16Wav(resampled, 16000);
    return bytesToBase64(wav);
  }

  cancel(): void {
    this.disconnectNodes();
    this.chunks = [];
    this.stopTracks();
    void this.audioContext?.close();
    this.audioContext = null;
  }

  private disconnectNodes(): void {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    if (this.processor) {
      this.processor.onaudioprocess = null;
    }
    this.processor = null;
    this.source = null;
    this.sink = null;
  }

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}

export function concatenateFloat32(chunks: Float32Array[]): Float32Array {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const result = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

export function resampleLinear(
  input: Float32Array,
  inputSampleRate: number,
  outputSampleRate: number
): Float32Array {
  if (inputSampleRate === outputSampleRate) {
    return input;
  }
  const ratio = inputSampleRate / outputSampleRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = index * ratio;
    const lower = Math.floor(sourceIndex);
    const upper = Math.min(lower + 1, input.length - 1);
    const weight = sourceIndex - lower;
    output[index] = input[lower] * (1 - weight) + input[upper] * weight;
  }
  return output;
}

export function encodePcm16Wav(samples: Float32Array, sampleRate: number): Uint8Array {
  const bytesPerSample = 2;
  const dataLength = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += bytesPerSample;
  }

  return new Uint8Array(buffer);
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function windowWithWebkit(): WindowWithWebkitAudio {
  return window as WindowWithWebkitAudio;
}
