import { useEffect, useRef, useState } from "react";

const MAX_RIPPLES = 3;
const RIPPLE_LIFETIME_MS = 600;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

export default function RippleButton({
  className = "",
  children,
  onPointerDown,
  onClick,
  ...props
}) {
  const [ripples, setRipples] = useState([]);
  const nextId = useRef(0);
  const timers = useRef(new Set());

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  function addRipple(button, x, y) {
    if (prefersReducedMotion() || button.matches(":disabled")) return;
    const rect = button.getBoundingClientRect();
    const size =
      2 * Math.hypot(Math.max(x, rect.width - x), Math.max(y, rect.height - y));
    const id = nextId.current++;
    setRipples((current) => [...current.slice(1 - MAX_RIPPLES), { id, x, y, size }]);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setRipples((current) => current.filter((ripple) => ripple.id !== id));
    }, RIPPLE_LIFETIME_MS);
    timers.current.add(timer);
  }

  function handlePointerDown(event) {
    onPointerDown?.(event);
    if (event.button === 0 || event.pointerType !== "mouse") {
      const rect = event.currentTarget.getBoundingClientRect();
      addRipple(event.currentTarget, event.clientX - rect.left, event.clientY - rect.top);
    }
  }

  function handleClick(event) {
    if (event.detail === 0) {
      const rect = event.currentTarget.getBoundingClientRect();
      addRipple(event.currentTarget, rect.width / 2, rect.height / 2);
    }
    onClick?.(event);
  }

  return (
    <button
      className={`${className} has-ripple`.trim()}
      onPointerDown={handlePointerDown}
      onClick={handleClick}
      {...props}
    >
      {children}
      <span className="ripple-layer" aria-hidden="true">
        {ripples.map((ripple) => (
          <span
            key={ripple.id}
            className="ripple"
            style={{
              left: ripple.x - ripple.size / 2,
              top: ripple.y - ripple.size / 2,
              width: ripple.size,
              height: ripple.size,
            }}
          />
        ))}
      </span>
    </button>
  );
}
