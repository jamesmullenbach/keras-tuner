import time
import json
import numpy as np
from os import path
from collections import defaultdict
from tensorflow.python.lib.io import file_io # allows to write to GCP or local
from termcolor import cprint
from keras import backend as K
import copy
from .execution import InstanceExecution
from .tunercallback import TunerCallback
from . import keraslyzer

class Instance(object):
  """Model instance class."""

  def __init__(self, idx, model, hyper_parameters, meta_data, num_gpu, batch_size, display_model, key_metrics, keras_function, save_models):
    self.ts = int(time.time())
    self.training_size = -1
    self.model = model
    self.hyper_parameters = hyper_parameters
    self.meta_data = meta_data
    self.save_models = save_models

    self.idx = idx
    self.meta_data['instance'] = idx

    self.num_gpu = num_gpu
    self.batch_size = batch_size #we keep batch_size explicit to be able to record it
    self.display_model = display_model
    self.ts = int(time.time())
    self.executions = []
    self.model_size = self.__compute_model_size(model)
    self.validation_size = 0
    self.results = {}
    self.key_metrics = key_metrics
    self.keras_function = keras_function

  def __get_instance_info(self):
    """Return a dictionary of the model parameters

      Used both for the instance result file and the execution result file
    """
    info = {
      "key_metrics": {}, #key metrics results dict. not key metrics definition
      "ts": self.ts,
      "training_size": self.training_size,
      #FIXME: add validation split if needed
      "validation_size": self.validation_size,
      "num_executions": len(self.executions),
      "model": json.loads(self.model.to_json()),
      "batch_size": self.batch_size,
      "model_size": int(self.model_size),
      "hyper_parameters": self.hyper_parameters
    }
    return info

  def __compute_model_size(self, model):
    "comput the size of a given model"
    return np.sum([K.count_params(p) for p in set(model.trainable_weights)])

  def fit(self, x, y, resume_execution=False, **kwargs):
    """Fit an execution of the model instance
    Args:
      resume_execution (bool): Instead of creating a new execution, resume training the previous one. Default false.
    """
    self.training_size = len(y)
    if kwargs.get('validation_data'):
      self.validation_size = len(kwargs['validation_data'][1])

    if resume_execution and len(self.executions):
      execution = self.executions[-1]
      #FIXME: merge accuracy back
      results = execution.fit(x, y, initial_epoch=execution.num_epochs ,**kwargs)
    else:
      execution = self.__new_execution()
      results  = execution.fit(x, y, **kwargs)
    # compute execution level metrics
    execution.record_results(results)
    return results

  def __new_execution(self):
    num_executions = len(self.executions)

    # ensure that info is only displayed once per iteration
    if num_executions > 0:
      display_model = None
      display_info = False
    else:
      display_info = True
      display_model = self.display_model

    instance_info = self.__get_instance_info()
    execution = InstanceExecution(self.model, self.idx, self.meta_data, self.num_gpu, 
                display_model, display_info, instance_info, self.key_metrics, 
                self.keras_function, self.save_models)
    self.executions.append(execution)
    return execution

  def record_results(self):
    """Record training results
    Returns:
      dict: results data
    """

    results = self.__get_instance_info()
    local_dir = self.meta_data['local_dir']
    #cprint(results, 'magenta')

    # collecting executions results
    exec_metrics = defaultdict(lambda : defaultdict(list))
    executions = [] # execution data
    for execution in self.executions:
        execution_id = execution.meta_data['execution']

         # metrics collection
        for metric, data in execution.metrics.items():
            exec_metrics[metric]['min'].append(execution.metrics[metric]['min'])
            exec_metrics[metric]['max'].append(execution.metrics[metric]['max'])

        # execution data
        execution_info = {
            "num_epochs": execution.num_epochs,
            "history": execution.history,
            "loss_fn": execution.model.loss,
            "loss_weigths": execution.model.loss_weights,
            "meta_data": execution.meta_data
            #FIXME record optimizer parameters
            #"optimizer": execution.model.optimizer
        }
        executions.append(execution_info)

    results['executions'] = executions
    results['meta_data'] = self.meta_data

    # aggregating statistics
    metrics = defaultdict(dict)
    for metric in exec_metrics.keys():
      for direction, data in exec_metrics[metric].items():
        metrics[metric][direction] = {
          "min": np.min(data),
          "max": np.max(data),
          "mean": np.mean(data),
          "median": np.median(data)
        }
    results['metrics'] = metrics

    #cprint(results, 'cyan')

    # Usual metrics reported as top fields for their median values
    for tm in self.key_metrics:
        if tm[0] in metrics:
            results['key_metrics'][tm[0]] = metrics[tm[0]][tm[1]]['median']

    fname = '%s-%s-%s-results.json' % (self.meta_data['project'], self.meta_data['architecture'], self.meta_data['instance'])
    local_path = path.join(local_dir, fname)
    with file_io.FileIO(local_path, 'w') as outfile:
        outfile.write(json.dumps(results))
    keraslyzer.cloud_save(local_path=local_path, ftype='results', meta_data=self.meta_data) #be sure to pass instance data

    self.results = results
    return results