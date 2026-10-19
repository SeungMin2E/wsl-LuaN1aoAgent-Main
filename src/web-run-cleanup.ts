/** Cleanup failures must not reject a detached web run's completion promise. */
export async function settleRunCleanup(
  cleanup: () => Promise<void>,
  onError: (error: unknown) => void
): Promise<void> {
  try {
    await cleanup();
  } catch (error) {
    onError(error);
  }
}
