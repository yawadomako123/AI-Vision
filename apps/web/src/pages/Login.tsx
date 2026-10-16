import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api } from "../api";
import { Button, ErrorNote, inputCls } from "../components/ui";

export default function Login({ mode, onDone }: { mode: "setup" | "login"; onDone: () => void }) {
  const setup = mode === "setup";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const submit = useMutation({
    mutationFn: () => (setup ? api.setup(username.trim(), password) : api.login(username.trim(), password)),
    onSuccess: onDone,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (setup && password !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    submit.mutate();
  };

  return (
    <main className="grid min-h-dvh place-items-center bg-bg p-4">
      <form onSubmit={onSubmit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-line bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="text-lg font-semibold">AI Vision Edge</span>
        </div>
        <div>
          <h1 className="text-base font-semibold">{setup ? "Create the admin account" : "Sign in"}</h1>
          {setup && (
            <p className="mt-1 text-sm text-muted">
              First run: this account manages cameras, rules and other users. Use a password of at least 10 characters.
            </p>
          )}
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-muted">Username</span>
          <input className={inputCls} value={username} onChange={(e) => setUsername(e.target.value)} required
                 autoComplete="username" autoFocus pattern="[A-Za-z0-9_.@\-]{3,60}"
                 title="3–60 letters, numbers, or _ . @ -" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs font-medium text-muted">Password</span>
          <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 required minLength={setup ? 10 : undefined} autoComplete={setup ? "new-password" : "current-password"} />
        </label>
        {setup && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-muted">Confirm password</span>
            <input className={inputCls} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                   required autoComplete="new-password" />
          </label>
        )}

        {mismatch && <ErrorNote error={new Error("Passwords don't match")} />}
        {submit.error && <ErrorNote error={submit.error} />}
        <Button type="submit" variant="primary" disabled={submit.isPending} className="py-2">
          {submit.isPending ? "Please wait…" : setup ? "Create account" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}
