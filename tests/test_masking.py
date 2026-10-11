from nightwatch.masking import Masker


def test_mask_and_unmask_roundtrip():
    m = Masker()
    raw = ('{"user":"alice@corp.com","arn":"arn:aws:iam::123456789012:user/devops/bob",'
           '"role":"arn:aws:sts::123456789012:assumed-role/Deploy/jane.doe","account":"123456789012",'
           '"public_ip":"13.234.10.7","private_ip":"10.0.1.5","rule":"tcp:443 <- 0.0.0.0/0,52.66.0.0/16",'
           '"instance":"i-0123456789abcdef0","sg":"sg-0ccc3333"}')
    out = m.mask(raw)
    for secret in ("alice@corp.com", "123456789012", "bob", "jane.doe", "13.234.10.7", "52.66.0.0"):
        assert secret not in out, secret
    for kept in ("10.0.1.5", "0.0.0.0/0", "i-0123456789abcdef0", "sg-0ccc3333", "assumed-role/Deploy/", ":user/devops/"):
        assert kept in out, kept
    assert "203.0.113." in out and "/16" in out
    assert m.mask(raw) == out                                   # stable
    assert m.unmask(out) == raw                                 # reversible
    acct = out.split('"account":"')[1].split('"')[0]
    assert m.unmask(f"arn:aws:iam::{acct}:role/x") == "arn:aws:iam::123456789012:role/x"


def test_aws_tool_wrapper_masks_output_and_unmasks_input(monkeypatch):
    from nightwatch.connectors import TOOLS, aws
    from nightwatch.masking import MASKER
    seen = {}

    class FakeIam:
        def simulate_principal_policy(self, **kw):
            seen.update(kw)
            return {"EvaluationResults": [{"EvalActionName": "ec2:StopInstances", "EvalResourceName": "*", "EvalDecision": "implicitDeny",
                                           "MatchedStatements": [{"SourcePolicyId": "arn:aws:iam::987654321098:policy/RO"}]}]}
    monkeypatch.setenv("NIGHTWATCH_AWS_PROFILE", "test")
    monkeypatch.setattr(aws, "_client", lambda s, r: FakeIam())
    masked_arn = MASKER.mask("arn:aws:iam::987654321098:user/carol")
    t = TOOLS["aws_iam_simulate"]
    out = t.fn(t.input_model.model_validate({"principal_arn": masked_arn, "actions": ["ec2:StopInstances"]}))
    assert seen["PolicySourceArn"] == "arn:aws:iam::987654321098:user/carol"   # real value reached AWS
    assert "987654321098" not in out.text and "implicitDeny" in out.text        # model sees placeholders
