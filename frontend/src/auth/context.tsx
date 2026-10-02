import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { Me } from "../api/types";
import type { Permission } from "./permissions";

const MeContext = createContext<Me | null>(null);

export function MeProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe must be used inside an authenticated route");
  return me;
}

/** Returns a predicate over the current user's permissions. */
export function useCan(): (permission: Permission) => boolean {
  const me = useContext(MeContext);
  return useMemo(() => {
    const granted = new Set(me?.permissions ?? []);
    return (permission: Permission) => granted.has(permission);
  }, [me]);
}
