async (page) => {
  const origin='http://127.0.0.1:38800';
  const arm=page.url().split('/')[3];
  await page.goto(origin+'/'+arm+'/');
  await page.getByRole('textbox',{name:'Goal',exact:true}).fill('UI-COMP '+arm+' RESTART');
  await page.getByRole('button',{name:'Create task',exact:true}).click();
  await page.waitForURL('**/'+arm+'/cmp-*');
  await page.getByRole('button',{name:'Start task',exact:true}).click();
  await page.getByTestId('host-status').filter({hasText:/^waiting_for_user$/}).waitFor({timeout:15000});
  const id=page.url().split('/').at(-1);
  const s=await (await page.request.get(origin+'/api/runs/'+id)).json();
  return {arm,run_id:id,url:page.url(),question_id:s.question.question_id,status:s.status};
}
