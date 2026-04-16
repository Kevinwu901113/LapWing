import { useEffect } from "react";
import { useTasksStore } from "@/stores/tasks";

/** Loads initial tasks on mount. SSE events update in real-time via useSSEv2 dispatch. */
export function useTasks() {
  const loadTasks = useTasksStore((s) => s.loadTasks);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  return useTasksStore();
}
