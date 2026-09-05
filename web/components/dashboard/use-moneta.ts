"use client"

import { useCallback, useEffect, useState } from "react"
import { ApiError } from "@/lib/api"

export type Loadable<T> = {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/**
 * Fetches once on mount and on explicit reload. The reconciliation is a finished
 * artefact, not a live feed, so nothing here polls — a controller looking at a match
 * rate that silently changes under them is worse than one who has to click refresh.
 */
export function useResource<T>(fetcher: () => Promise<T>, deps: unknown[] = []): Loadable<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // The fetcher is usually an inline arrow, so it is a new reference every render.
  // Keying the effect on the caller's deps plus a reload nonce avoids a fetch loop.
  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetcher()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload }
}
