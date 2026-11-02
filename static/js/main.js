document.addEventListener("DOMContentLoaded", () => {
  const harvestDate = document.querySelector('input[name="harvest_date"]');
  if (harvestDate) harvestDate.max = new Date().toISOString().slice(0, 10);
  document.querySelectorAll(".alert").forEach((alert) => {
    window.setTimeout(() => {
      if (window.bootstrap) bootstrap.Alert.getOrCreateInstance(alert).close();
    }, 6000);
  });
});
