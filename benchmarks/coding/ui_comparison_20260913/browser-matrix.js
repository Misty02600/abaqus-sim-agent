async (page) => {
  const arm = page.url().split('/')[3];
  if (!['official', 'external', 'agui'].includes(arm)) throw new Error('Open an arm first');
  const origin = 'http://127.0.0.1:38800';
  const report = { arm, scenarios: [], errors: [], browserRequests: [] };
  try {
  const attach = p => {
    p.on('pageerror', e => report.errors.push(String(e)));
    p.on('request', r => {
      if (r.method() === 'POST' && r.url().startsWith(origin)) {
        report.browserRequests.push({url:r.url(),body:r.postDataJSON()});
      }
    });
  };
  attach(page);
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const host = async id => (await page.request.get(origin + '/api/runs/' + id)).json();
  const waitStatus = async (p, status) => {
    await p.getByTestId('host-status').filter({hasText:new RegExp('^'+status+'$')}).waitFor({timeout:20000});
  };
  const create = async kind => {
    await page.goto(origin+'/'+arm+'/');
    await page.getByRole('textbox',{name:'Goal',exact:true}).fill('UI-COMP '+arm+' '+kind);
    await page.getByRole('button',{name:'Create task',exact:true}).click();
    await page.waitForURL('**/'+arm+'/cmp-*',{timeout:10000});
    const runId=page.url().split('/').at(-1);
    await waitStatus(page,'registered');
    await page.getByRole('button',{name:'Start task',exact:true}).click();
    await waitStatus(page,'waiting_for_user');
    return runId;
  };
  const answer = async p => {
    await p.getByRole('textbox',{name:'Answer',exact:true}).fill('1 mm');
    await p.getByRole('button',{name:'Submit answer',exact:true}).click();
  };
  const forbidden = async p => {
    const count=await p.getByRole('button',{name:/^(Retry|Edit|Edit message|Fork|Delete|Cancel|Stop|Regenerate)$/i}).count();
    check(count===0,'Unsupported action exposed: '+count);
  };
  const assertResult = async (id,status) => {
    const s=await host(id);
    check(s.status===status,'Wrong host outcome');
    check(s.outcome.usage.model_requests===3,'Unexpected model request count');
    check(s.outcome.usage.abaqus_calls===0&&s.outcome.usage.solver_calls===0,'Unexpected runtime');
    check(s.outcome.verification_scope==='execution_evidence','Scope mismatch');
    return s;
  };

  const passId=await create('PASS');
  const before=await host(passId);
  await page.reload(); await waitStatus(page,'waiting_for_user');
  check((await host(passId)).question.question_id===before.question.question_id,'Question changed on refresh');
  const separate=await page.context().browser().newContext();
  const other=await separate.newPage(); attach(other);
  await other.goto(origin+'/'+arm+'/'+passId); await waitStatus(other,'waiting_for_user');
  await forbidden(other);
  await answer(other); await waitStatus(other,'completed'); await waitStatus(page,'completed');
  const completed=await assertResult(passId,'completed');
  check(await page.getByTestId('host-scope').textContent()==='execution_evidence','Scope not rendered');
  check((await page.getByTestId('host-verdict').textContent()).toLowerCase().includes('pass'),'Host pass not rendered');
  await forbidden(page);
  check(!(await page.locator('body').innerText()).includes('ask_user Running'),'Question still appears running');
  await page.screenshot({path:'../../output/playwright/ui-comparison-'+arm+'-completed.png',fullPage:true});
  const answerRequest=report.browserRequests.findLast(r=>r.url.endsWith('/'+passId+'/answer')||(r.url.endsWith('/api/agui/'+passId)&&r.body.resume?.length));
  check(!!answerRequest,'No actual answer transport captured');
  const repeat=await page.request.post(answerRequest.url,{data:answerRequest.body});
  check(repeat.ok(),'Duplicate request rejected');
  const same=await assertResult(passId,'completed');
  check(JSON.stringify(same.outcome)===JSON.stringify(completed.outcome),'Duplicate changed outcome');
  const conflict=JSON.parse(JSON.stringify(answerRequest.body));
  if(arm==='agui'){conflict.runId='conflict-'+passId;conflict.resume[0].payload.answer='2 mm';}
  else{conflict.command_id='conflict-'+passId;conflict.answer='2 mm';}
  const rejected=await page.request.post(answerRequest.url,{data:conflict});
  check(rejected.status()===409,'Conflicting answer not rejected');
  const unsupported=await page.request.post(origin+'/api/runs/'+passId+'/cancel',{data:{command_id:'unsupported'}});
  check(unsupported.status()>=400&&unsupported.status()<500,'Unsupported command accepted');
  report.scenarios.push({kind:'PASS',run_id:passId,refresh:true,freshBrowser:true,hostCompleted:true,duplicateStatus:repeat.status(),conflictStatus:rejected.status(),forbiddenCommandStatus:unsupported.status(),modelRequests:3,abaqusCalls:0});
  await separate.close();

  const failId=await create('FAIL');
  await answer(page); await waitStatus(page,'failed'); await assertResult(failId,'failed');
  check((await page.getByTestId('host-verdict').textContent()).toLowerCase().includes('fail'),'Host failure not rendered');
  check((await page.locator('body').innerText()).includes('MODEL_SAYS_COMPLETE'),'Model completion text not present for contrast');
  check((await page.locator('body').innerText()).includes('missing.txt'),'Host rejection detail absent');
  await forbidden(page);
  await page.screenshot({path:'../../output/playwright/ui-comparison-'+arm+'-failed.png',fullPage:true});
  report.scenarios.push({kind:'FAIL',run_id:failId,modelCompletionRejected:true,modelRequests:3,abaqusCalls:0});

  const slowId=await create('SLOW');
  await answer(page); await waitStatus(page,'running');
  await page.goto('about:blank');
  let slow;
  for(let n=0;n<100;n++){
    slow=await host(slowId);
    if(slow.status!=='running')break;
    await page.waitForTimeout(150);
  }
  check(slow.status==='completed','Disconnected host did not complete');
  await page.goto(origin+'/'+arm+'/'+slowId); await waitStatus(page,'completed');
  await assertResult(slowId,'completed'); await forbidden(page);
  report.scenarios.push({kind:'SLOW',run_id:slowId,disconnectDidNotCancel:true,reopenCompleted:true,modelRequests:3,abaqusCalls:0});
  check(report.errors.length===0,'Browser errors: '+report.errors.join('\n'));
  report.passed=true;
  return report;
  } catch(error) {
    return {...report,passed:false,error:String(error),url:page.url()};
  }
}
