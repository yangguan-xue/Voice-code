import { useEffect, useMemo, useRef, useState } from "react";
import { VoiceWindow } from "../features/voice/VoiceWindow";
import {
  subscribeToVoicePermissionResolutions,
  subscribeToVoiceUiEvents
} from "../features/voice/voice-broadcast";
import { createDesktopVoiceClient } from "../features/voice/voice-client";
import { describeVoiceError } from "../features/voice/voice-errors";
import { BrowserVoiceRecorder } from "../features/voice/voice-recorder";
import {
  clearVoiceTranscript,
  createInitialVoiceState,
  toggleTtsMuted,
  voiceReducer
} from "../features/voice/voice-reducer";
import { readVoiceLaunchContext } from "../features/voice/voice-launch-context";
import {
  broadcastVoiceExecutionState,
  subscribeToVoiceContextSnapshots,
  subscribeToVoiceInterruptRequests,
  type VoiceContextSnapshot
} from "../features/voice/voice-context-sync";

export function VoiceWindowApp() {
  const [voiceState, setVoiceState] = useState(createInitialVoiceState);
  const [draft, setDraft] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [bridgeError, setBridgeError] = useState("");
  const [context, setContext] = useState(readVoiceLaunchContext);
  const [pendingContext, setPendingContext] = useState<VoiceContextSnapshot | null>(null);
  const isBusyRef = useRef(false);
  const contextRef = useRef(context);
  const pendingContextRef = useRef<VoiceContextSnapshot | null>(null);
  const recorderRef = useRef<BrowserVoiceRecorder | null>(null);
  const voiceClient = useMemo(
    () => createDesktopVoiceClient((event) => setVoiceState((current) => voiceReducer(current, event))),
    []
  );

  useEffect(() => {
    contextRef.current = context;
  }, [context]);

  useEffect(() => {
    pendingContextRef.current = pendingContext;
  }, [pendingContext]);

  useEffect(() => {
    isBusyRef.current = isBusy;
    broadcastVoiceExecutionState({
      isBusy,
      mode: context.mode,
      sourceSessionId: context.sourceSessionId
    });
  }, [context.mode, context.sourceSessionId, isBusy]);

  useEffect(() => {
    return subscribeToVoiceUiEvents((event) => {
      setVoiceState((current) => voiceReducer(current, event));
    });
  }, []);

  useEffect(() => {
    return subscribeToVoiceContextSnapshots((snapshot) => {
      const current = contextRef.current;
      if (
        current.mode !== "sharedSession" ||
        snapshot.mode !== "sharedSession" ||
        snapshot.sourceSessionId !== current.sourceSessionId ||
        snapshot.contextVersion <= current.contextVersion
      ) {
        return;
      }

      if (isBusyRef.current) {
        setPendingContext(snapshot);
        return;
      }
      setContext(snapshot);
      setPendingContext(null);
    });
  }, []);

  useEffect(() => {
    return subscribeToVoicePermissionResolutions((resolution) => {
      void voiceClient.resolvePermission(resolution).catch((error: unknown) => {
        setBridgeError(errorMessage(error));
      });
    });
  }, [voiceClient]);

  useEffect(() => {
    return subscribeToVoiceInterruptRequests((request) => {
      if (
        !isBusyRef.current ||
        request.sourceSessionId !== contextRef.current.sourceSessionId
      ) {
        return;
      }
      void voiceClient.interruptTurn().catch((error: unknown) => {
        setBridgeError(errorMessage(error));
      });
    });
  }, [voiceClient]);

  useEffect(() => {
    let mounted = true;
    void voiceClient
      .connect()
      .then((config) => {
        if (!mounted) {
          return;
        }
        setBridgeError("");
        setVoiceState((current) =>
          config.ttsMuted === current.ttsMuted ? current : toggleTtsMuted(current)
        );
      })
      .catch((error: unknown) => {
        if (mounted) {
          const message = errorMessage(error);
          setBridgeError(message);
          setVoiceState((current) =>
            voiceReducer(current, {
              type: "voice.error",
              message,
              recoverable: true
            })
          );
        }
      });

    return () => {
      mounted = false;
      voiceClient.close();
    };
  }, [voiceClient]);

  function startListening() {
    const recorder = recorderRef.current ?? new BrowserVoiceRecorder();
    recorderRef.current = recorder;
    setBridgeError("");
    setVoiceState((current) =>
      voiceReducer(current, { type: "voice.state", state: "listening", text: "我在听" })
    );
    void recorder.start().catch((error: unknown) => {
      const message = errorMessage(error);
      recorderRef.current = null;
      setBridgeError(message);
      setVoiceState((current) =>
        voiceReducer(current, {
          type: "voice.error",
          message,
          recoverable: true
        })
      );
    });
  }

  function stopListening() {
    if (voiceState.status === "listening") {
      startBusy();
      setBridgeError("");
      setVoiceState((current) =>
        voiceReducer(current, { type: "voice.state", state: "transcribing", text: "正在识别" })
      );
      void recorderRef.current
        ?.stop()
        .then((audioBase64) => voiceClient.transcribeAudio(audioBase64))
        .then((text) => {
          if (text.trim()) {
            return voiceClient.startTurn(text, {
              mode: context.mode,
              contextVersion: context.contextVersion,
              contextMessages: context.contextMessages,
              sourceSessionId: context.sourceSessionId,
              workspacePath: context.workspacePath
            });
          }
          throw new Error("没有识别到有效语音。");
        })
        .catch((error: unknown) => {
          const message = errorMessage(error);
          setBridgeError(message);
          setVoiceState((current) =>
            voiceReducer(current, {
              type: "voice.error",
              message,
              recoverable: true
            })
          );
        })
        .finally(() => {
          recorderRef.current = null;
          finishBusy();
        });
      return;
    }

    if (
      !isBusy &&
      voiceState.status !== "thinking" &&
      voiceState.status !== "transcribing" &&
      voiceState.status !== "speaking"
    ) {
      return;
    }

    recorderRef.current?.cancel();
    recorderRef.current = null;
    void voiceClient.interruptTurn().catch((error: unknown) => setBridgeError(errorMessage(error)));
    setVoiceState((current) =>
      voiceReducer(current, { type: "voice.state", state: "paused", text: "已暂停" })
    );
  }

  function toggleMute() {
    const nextMuted = !voiceState.ttsMuted;
    setVoiceState((current) => toggleTtsMuted(current));
    void voiceClient.setTtsMuted(nextMuted).catch((error: unknown) => {
      setBridgeError(errorMessage(error));
      setVoiceState((current) => toggleTtsMuted(current));
    });
  }

  function clearTranscript() {
    setVoiceState((current) => clearVoiceTranscript(current));
  }

  function closeVoiceWindow() {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    voiceClient.close();
    window.close();
  }

  function submitVoiceText() {
    const text = draft.trim();
    if (!text || isBusy) {
      return;
    }

    setDraft("");
    startBusy();
    setBridgeError("");
    void voiceClient
      .startTurn(text, {
        mode: context.mode,
        contextVersion: context.contextVersion,
        contextMessages: context.contextMessages,
        sourceSessionId: context.sourceSessionId,
        workspacePath: context.workspacePath
      })
      .catch((error: unknown) => {
        const message = errorMessage(error);
        setBridgeError(message);
        setVoiceState((current) =>
          voiceReducer(current, {
            type: "voice.error",
            message,
            recoverable: true
          })
        );
      })
      .finally(finishBusy);
  }

  function startBusy() {
    setIsBusy(true);
  }

  function finishBusy() {
    const pending = pendingContextRef.current;
    setIsBusy(false);
    if (pending) {
      setContext(pending);
      setPendingContext(null);
    }
  }

  return (
    <VoiceWindow
      state={voiceState}
      workspaceLabel={context.workspaceLabel}
      branch={context.branch}
      contextMode={context.mode}
      sourceSessionTitle={context.sourceSessionTitle}
      contextMessageCount={context.contextMessages.length}
      contextVersion={context.contextVersion}
      contextSyncStatus={pendingContext ? "pending" : "synced"}
      draft={draft}
      isBusy={isBusy}
      bridgeError={bridgeError}
      onDraftChange={setDraft}
      onSubmitDraft={submitVoiceText}
      onStartListening={startListening}
      onStopListening={stopListening}
      onToggleMute={toggleMute}
      onClearTranscript={clearTranscript}
      onClose={closeVoiceWindow}
    />
  );
}

function errorMessage(error: unknown): string {
  return describeVoiceError(error);
}
