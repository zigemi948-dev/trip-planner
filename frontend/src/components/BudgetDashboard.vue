<script setup lang="ts">
import type { BudgetBreakdown } from '../types/trip';

defineProps<{
  budget: BudgetBreakdown;
}>();
</script>

<template>
  <!-- Compact budget ledger matching the backend BudgetBreakdown fields. -->
  <section class="panel budget">
    <h2>Budget</h2>
    <div class="budget-total">
      <strong>¥{{ budget.total_cost.toFixed(2) }}</strong>
      <span>/ ¥{{ budget.budget_limit.toFixed(2) }}</span>
    </div>
    <dl>
      <div>
        <dt>Tickets</dt>
        <dd>¥{{ budget.fixed_cost.toFixed(2) }}</dd>
      </div>
      <div>
        <dt>Transport</dt>
        <dd>¥{{ budget.transport_cost.toFixed(2) }}</dd>
      </div>
      <div>
        <dt>Food</dt>
        <dd>¥{{ budget.food_cost.toFixed(2) }}</dd>
      </div>
      <div>
        <dt>Remaining</dt>
        <dd :class="{ danger: budget.remaining < 0 }">¥{{ budget.remaining.toFixed(2) }}</dd>
      </div>
    </dl>
  </section>
</template>
