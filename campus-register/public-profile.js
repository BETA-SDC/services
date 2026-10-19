(() => {
  const slug = decodeURIComponent(location.pathname.slice(1));
  let canonical = slug;
  document.addEventListener("beta-session", event => {
    const user = event.detail.user;
    if (!slug && user) location.replace("/" + encodeURIComponent(user.profile.slug));
    document.querySelector("#public-edit").hidden = !user || user.profile.slug !== canonical;
  });
  if (!slug) {
    document.querySelector("#public-bio").textContent = "登录后查看你的个人主页。";
    return;
  }
  (async () => {
    try {
      const response = await fetch("/api/public-profile/" + encodeURIComponent(slug), {cache: "no-store"});
      const profile = await response.json();
      if (!response.ok) throw new Error(profile.error || "个人主页暂不可用。");
      canonical = profile.slug;
      if (canonical !== slug) history.replaceState({}, "", "/" + encodeURIComponent(canonical));
      document.title = profile.name + " · BETA";
      document.querySelector("#public-name").textContent = profile.name;
      document.querySelector("#public-handle").textContent = profile.slug;
      document.querySelector("#public-bio").textContent = profile.bio || "暂未填写个人简介。";
      document.querySelector("#public-avatar").src = profile.avatar;
      if (profile.github) {
        const link = document.querySelector("#public-github");
        link.href = profile.github;
        link.hidden = false;
      }
      const own = window.BetaShell?.state?.user;
      document.querySelector("#public-edit").hidden = !own || own.profile.slug !== canonical;
    } catch (error) {
      document.querySelector("#public-name").textContent = "没有找到个人主页";
      document.querySelector("#public-bio").textContent = "";
      document.querySelector("#public-error").textContent = error.message;
      document.querySelector("#public-error").hidden = false;
    }
  })();
})();
