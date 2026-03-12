/**
 * User authentication state management with Svelte 5 runes.
 *
 * Provides reactive user state, loading indicators, and authentication
 * actions for Moose Sports Empire frontend components.
 */

import { navigate } from "astro:transitions/client";
import { api } from "./api";

/** User profile data structure from backend API */
interface User {
  id: number;
  yahoo_guid: string;
  display_name: string;
  role: string;
}

let currentUser = $state<User | null>(null);
let isLoading = $state(true);

export function getUser() {
  return currentUser;
}

export function getIsLoading() {
  return isLoading;
}

/**
 * Fetch current user from authentication endpoint.
 *
 * Updates reactive state and handles authentication errors gracefully.
 * Called on app initialization and after login/logout actions.
 */
export async function fetchUser() {
  isLoading = true;
  try {
    currentUser = await api.get<User>("/auth/me");
  } catch {
    // User not authenticated or token expired
    currentUser = null;
  } finally {
    isLoading = false;
  }
}

/**
 * Log out user and clear authentication state.
 *
 * Calls backend logout endpoint, clears local state, and redirects
 * to home page. Ignores API errors to ensure logout completes.
 */
export async function logout() {
  try {
    await api.post("/auth/logout");
  } catch {
    // Ignore API errors - logout should complete regardless
  }
  currentUser = null;
  navigate("/", { history: "replace" });
}
