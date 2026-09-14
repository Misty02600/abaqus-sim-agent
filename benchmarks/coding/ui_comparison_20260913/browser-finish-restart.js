async (page) => {
  const origin='http://127.0.0.1:38800';
  const arm=page.url().split('/')[3], id=page.url().split('/').at(-1);
  const posts=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().startsWith(origin))posts.push({url:r.url(),body:r.postDataJSON()});});
  await page.reload();
  await page.getByTestId('host-status').filter({hasText:/^waiting_for_user$/}).waitFor({timeout:15000});
  if(posts.length)throw new Error('Reload performed writes');
  const before=await (await page.request.get(origin+'/api/runs/'+id)).json();
  await page.getByRole('textbox',{name:'Answer',exact:true}).fill('1 mm');
  await page.getByRole('button',{name:'Submit answer',exact:true}).click();
  await page.getByTestId('host-status').filter({hasText:/^completed$/}).waitFor({timeout:15000});
  const after=await (await page.request.get(origin+'/api/runs/'+id)).json();
  if(after.outcome.usage.model_requests!==3||after.outcome.usage.abaqus_calls!==0)throw new Error('Unexpected counts');
  if(before.question.question_id!==after.question.question_id)throw new Error('Question changed');
  return {arm,run_id:id,question_id:after.question.question_id,status:after.status,reloadWithoutWrite:true,modelRequests:3,abaqusCalls:0,browserRequests:posts,passed:true};
}
