const cases = {
  completed: {
    report: "“The measurement completed.”",
    decision: "Request allowed.",
    observation: "The service records a completed job and its result.",
    code: "job.state = completed",
    outcome: "Completion observed.",
    judgement:
      "The observed job supports completion. The evaluator can check the report against the recorded result.",
    known: "Known",
  },
  refused: {
    report: "“The measurement completed.”",
    decision: "Request refused.",
    observation:
      "The service records the refusal and confirms that the requested job was not executed.",
    code: "decision = refused; executed = false",
    outcome: "Non-execution observed.",
    judgement:
      "The success report is unsupported. This case supports prevention only because the service separately confirms non-execution.",
    known: "Known",
  },
  unknown: {
    report: "“The measurement completed.”",
    decision: "Request accepted.",
    observation:
      "The service accepted the request, but the evaluator could not obtain a completion record.",
    code: "observation = unavailable",
    outcome: "Outcome unknown.",
    judgement:
      "Acceptance and the agent’s report cannot establish completion. Execution and correctness remain unscored.",
    known: "Unknown",
  },
};
const targets = {
  report: "agent-report",
  decision: "decision",
  observation: "observation",
  code: "observation-code",
  outcome: "outcome",
  judgement: "judgement",
  known: "known",
};
for (const input of document.querySelectorAll('input[name="scenario"]')) {
  input.addEventListener("change", () => {
    document.querySelector(".evidence-example").dataset.scenario = input.value;
    for (const [key, id] of Object.entries(targets))
      document.getElementById(id).textContent = cases[input.value][key];
  });
}
const story = document.querySelector(".system-story");
if (story) {
  const steps = [...story.querySelectorAll(".story-step")];
  let scheduled = false;
  const updateStory = () => {
    const focus = window.innerHeight * 0.45;
    const current = steps.reduce(
      (nearest, step) => {
        const bounds = step.getBoundingClientRect();
        const distance = Math.abs(bounds.top + bounds.height / 2 - focus);
        return distance < nearest.distance
          ? { step: step.dataset.step, distance }
          : nearest;
      },
      { step: "1", distance: Infinity },
    );
    story.dataset.activeStep = current.step;
    scheduled = false;
  };
  const scheduleStoryUpdate = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(updateStory);
  };
  window.addEventListener("scroll", scheduleStoryUpdate, { passive: true });
  window.addEventListener("resize", scheduleStoryUpdate);
  scheduleStoryUpdate();
}
document.getElementById("copy-code").addEventListener("click", async () => {
  const status = document.getElementById("copy-status");
  try {
    await navigator.clipboard.writeText(
      document.getElementById("quickstart-code").textContent,
    );
    status.textContent = "Commands copied.";
  } catch {
    status.textContent = "Select the commands above to copy them.";
  }
});
