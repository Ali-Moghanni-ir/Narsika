(() => {
 const N=window.Narsika,form=N.$('#auth-form');
 N.$('#toggle-password')?.addEventListener('click',()=>{const field=N.$('#password');field.type=field.type==='password'?'text':'password';N.$('#toggle-password').setAttribute('aria-label',field.type==='password'?'Show password':'Hide password');});
 if(form)form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{const result=await N.apiFetch(form.action,{method:'POST',body:Object.fromEntries(new FormData(form))});location.href=result.data.redirect;}catch(error){N.error(form,error.message);}finally{button.disabled=false;}};
})();
