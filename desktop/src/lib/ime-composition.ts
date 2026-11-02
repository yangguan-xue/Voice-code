import { useCallback, useRef } from "react";

/**
 * Track IME composition state manually.
 *
 * Chrome fires the composition-confirming Enter keydown with
 * `nativeEvent.isComposing === true`, but WKWebView/Safari dispatch the
 * Enter keydown *after* `compositionend`, where `isComposing` is already
 * false. An async flag reset races the trailing keydown (timer tasks can
 * run before the keyboard event dispatch), so instead we record the
 * composition end time and ignore Enter within a short grace window.
 * `keyCode === 229` covers IMEs that never fire composition events.
 */
const COMPOSITION_ENTER_GRACE_MS = 150;

type CompositionAwareEvent = {
  nativeEvent: KeyboardEvent & { isComposing?: boolean };
};

export function useImeCompositionGuard() {
  const composingRef = useRef(false);
  const lastCompositionEndAtRef = useRef(0);

  const onCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);

  const onCompositionEnd = useCallback(() => {
    composingRef.current = false;
    lastCompositionEndAtRef.current = Date.now();
  }, []);

  const isCompositionEnter = useCallback((event: CompositionAwareEvent) => {
    const nativeEvent = event.nativeEvent;
    if (nativeEvent.isComposing || nativeEvent.keyCode === 229) {
      return true;
    }
    if (composingRef.current) {
      return true;
    }
    return Date.now() - lastCompositionEndAtRef.current < COMPOSITION_ENTER_GRACE_MS;
  }, []);

  return { isCompositionEnter, onCompositionStart, onCompositionEnd };
}
