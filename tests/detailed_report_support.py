"""Synthetic report outputs for contract tests, never live coaching fallbacks."""
def detailed_output(payload):
    criteria=[a['criterion'] for a in payload['acceptedFindings']]
    paragraphs=payload['paragraphs']
    return {
        'criterionAnalyses':[{'criterion':c,'analysis':'已呈现明确的信息，但展开仍需加强。',
            'strengths':['中心信息明确。'],'limitations':['支持信息的关系需要说明。']} for c in criteria],
        'priorities':[],
        'mindMap':{'thesis':'文章描述了题目中的主要变化。','branches':[{'paragraphIndex':p['index'],
            'role':'描述变化','point':p['text'],'support':[],'gap':''} for p in paragraphs]},
        'paragraphs':[{'index':p['index'],'role':'描述变化','assessment':'趋势明确，保留原有表达。',
            'impact':'SUPPORTS','corrections':[],'changes':[],'targetBandEstimate':None,
            'estimateReason':'单段不足以独立评分。'} for p in paragraphs],
        'topicLearning':{'theme':'变化与发展','expressions':[],'examples':[]},
        'encouragement':'你明确写出了主要变化，下一步练习信息之间的比较。',
        'nextActions':[{'title':'比较两个主要特征','minutes':10,'steps':['找出两个主要特征，写出比较句。'],
            'successCheck':'比较句与原始材料一致。'}]}
