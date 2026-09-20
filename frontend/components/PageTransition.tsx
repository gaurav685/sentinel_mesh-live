"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";

/** The one page-mount transition, reused by every page instead of each
 * page inventing its own fade/slide -- the reason a first pass could feel
 * like "isolated animated components" rather than a system. */
export function PageTransition({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}
